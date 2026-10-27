import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTION_SIGNATURE_COVERAGE_V0,
  ACTION_SIGNATURE_COVERAGE_V3,
  buildTurnEnvelope,
  assertTurnEnvelopeConsumerVersion,
  evaluateTurnEnvelope,
  quotaActionSignatureDocument,
  turnEnvelopeActionSignatureDocument,
  turnEnvelopeReplanSettlementIdentity,
} from "../../loopx/control_plane/quota/turn_envelope.ts";
import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { TURN_ENVELOPE_SECTION_TARGETS } from "../../loopx/control_plane/quota/turn_envelope_budget.ts";
import { projectPeerOrchestration } from "../../loopx/control_plane/quota/peer_orchestration.ts";

test("task text commitment has one selected-Todo carrier without changing legacy context elision", () => {
  for (const retainDetail of [false, true]) {
    const source = payload();
    const channel = (source.interaction_contract as JsonObject).agent_channel as JsonObject;
    channel.work_context = {complete: true, selected_todo_ref: "selected_todo",
      instruction: "Read full task requirements.", selected_todo_text_verified: true,
      ...(retainDetail ? {sources: [{source: "selected_todo", content: {todo: {note: "Retain evidence."}}}]} : {})};
    const envelope = buildTurnEnvelope({payload: source,
       scheduler_execution_args: ""});
    const selected = (envelope.action as JsonObject).selected_todo as JsonObject;
    assert.equal(selected.text_source_verified, true);
    assert.equal(envelope.work_context === undefined, !retainDetail);
    assert.equal((envelope.work_context as JsonObject | undefined)?.selected_todo_text_verified, undefined);
    assert.equal(JSON.stringify(envelope).split('"text_source_verified"').length - 1, 1);
    assert.equal((envelope.action_signature as JsonObject).matches, true);
    const legacy = structuredClone(source);
    delete (((legacy.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).selected_todo_text_verified;
    const oldEnvelope = buildTurnEnvelope({payload: legacy,
       scheduler_execution_args: ""});
    assert.equal(((oldEnvelope.action as JsonObject).selected_todo as JsonObject).text_source_verified, undefined);
    assert.equal((oldEnvelope.action_signature as JsonObject).matches, true);
    assert.notEqual((oldEnvelope.action_signature as JsonObject).source_hash,
      (envelope.action_signature as JsonObject).source_hash);
  }
});

function payload(): Record<string, unknown> {
  return {
    ok: true,
    goal_id: "goal-turn-envelope",
    agent_id: "agent-ts",
    agent_identity: { agent_id: "agent-ts" },
    decision: "run",
    should_run: true,
    effective_action: "normal_run",
    state: "eligible",
    reason: "one bounded segment is eligible",
    action_required: false,
    open_count: 0,
    recommended_action: "advance one bounded segment",
    selected_todo: {
      todo_id: "todo-1",
      status: "open",
      text: "advance one bounded segment",
      task_repository: "repo",
    },
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      mode: "bounded_delivery",
      user_channel: { action_required: false, notify: "DONT_NOTIFY" },
      agent_channel: {
        must_attempt: true,
        delivery_allowed: true,
        quiet_noop_allowed: false,
        primary_action: "advance one bounded segment",
      },
      cli_channel: {
        next_cli_actions: ["loopx refresh-state --goal-id goal-turn-envelope"],
        spend_allowed_now: false,
        spend_after_validation: true,
        spend_policy: "spend once after validated writeback",
      },
    },
    work_lane_contract: {
      schema_version: "work_lane_contract_v0",
      must_attempt_work: true,
    },
    goal_boundary: { rule: "stay_in_scope_or_stop", write_scope: ["loopx/**"] },
  };
}

test("execution contracts retain complete clauses, nested data and empty values", () => {
  const source = payload();
  const clause = "Preserve original identity / 保留原身份. ".repeat(40) + "Stop on source mismatch.";
  const contracts = {
    work_lane_contract: {schema_version: "work_lane_contract_v1", action: clause,
      outcome_followthrough: {required: true, spend_policy: clause,
        accepted_resolution_kinds: Array.from({length: 12}, (_, i) => `resolution-${i}`),
        evidence: {ordered: ["artifact", "validation"], missing: null}}},
    execution_obligation: {must_attempt_work: true, reason: clause, notify_is_execution_gate: false},
    automation_liveness: {keep_active: true, pause_allowed: false, pause_policy: clause,
      spend_policy: "Only after validated writeback", reason: "", observation: null},
    agent_scope_frontier: {recommended_action: clause, spend_policy: clause, blocks_delivery: false},
    autonomous_replan_scope: {required: false, applies: false, owner_agent_ids: [], context: null},
  };
  Object.assign(source, structuredClone(contracts));
  const envelope = buildTurnEnvelope({payload: source, scheduler_execution_args: ""});
  const capsule = envelope.contract_capsule as JsonObject;
  for (const [field, expected] of Object.entries(contracts)) {
    assert.deepEqual(capsule[field], expected);
    const changed = structuredClone(envelope);
    delete (changed.contract_capsule as JsonObject)[field];
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed),
      turnEnvelopeActionSignatureDocument(envelope));
  }
  // Retained future metadata is data, not a newly interpreted execution rule.
  assert.equal(envelope.should_run, true);
  assert.equal((envelope.compaction as JsonObject).within_budget, false);
  // Existing historical envelopes are checked from their captured content;
  // signature verification never rebuilds an old capsule through the producer.
  const historical = structuredClone(envelope);
  historical.contract_capsule = {schema_version: "loopx_contract_capsule_v0",
    source: "full_quota_decision", work_lane_contract: {must_attempt_work: true}};
  const signatureBefore = turnEnvelopeActionSignatureDocument(historical);
  (source.work_lane_contract as JsonObject).action = "changed later";
  assert.deepEqual(turnEnvelopeActionSignatureDocument(historical), signatureBefore);
  assert.deepEqual(capsule.work_lane_contract, contracts.work_lane_contract);
});

test("a Todo-less planning identity must match the original signed quota binding", () => {
  const identity = {
    schema_version: "quota_settlement_identity_v1", goal_id: "goal-turn-envelope", agent_id: "agent-ts",
    turn_instance_id: "planning-turn", binding_kind: "autonomous_replan", binding_id: "replan-original",
    replan_obligation_id: "replan-original",
    effect_id: "goal-turn-envelope:agent-ts:autonomous_replan:replan-original:planning-turn",
  };
  const source = payload();
  source.selected_todo = null;
  source.effective_action = "autonomous_replan_required";
  source.replan_action_packet = {schema_version: "replan_action_packet_v0", obligation_id: "replan-original"};
  const cli = (source.interaction_contract as JsonObject).cli_channel as JsonObject;
  cli.settlement_plan = {schema_version: "quota_settlement_plan_v1", identity, ordered_steps: []};
  const envelope = buildTurnEnvelope({payload: source,  scheduler_execution_args: ""});
  assert.deepEqual(turnEnvelopeReplanSettlementIdentity(envelope, "planning-turn"), identity);
  assert.throws(() => turnEnvelopeReplanSettlementIdentity(envelope, "different-turn"), /quota admission/);
  for (const field of ["goal_id", "agent_id", "replan_obligation_id", "effect_id"]) {
    const changed = structuredClone(source);
    const changedCli = (changed.interaction_contract as JsonObject).cli_channel as JsonObject;
    ((changedCli.settlement_plan as JsonObject).identity as JsonObject)[field] = "different";
    // Native rebuilding signs the changed bytes too; semantic binding must
    // still fail rather than treating integrity as execution authority.
    const changedEnvelope = buildTurnEnvelope({payload: changed,  scheduler_execution_args: ""});
    assert.throws(() => turnEnvelopeReplanSettlementIdentity(changedEnvelope, "planning-turn"));
  }
  assert.equal(turnEnvelopeReplanSettlementIdentity(buildTurnEnvelope({
    payload: payload(),  scheduler_execution_args: "",
  })), null);
});



test("work profile retains outcome rules, ordered requirements and signature coverage", () => {
  const source = payload();
  const profile = {
    cadence: "bounded_progress_segment", minimum_scale: "multi_surface_or_implementation",
    must_include: ["coherent_artifact", "targeted_validation", "state_writeback"],
    spend_rule: "spend_only_after_artifact_validation_writeback",
    outcome_floor: {configured: true, surface_streak_threshold: 3,
      must_advance: ["primary_goal_outcome"],
      avoid: ["Retain the complete accepted outcome. " + "完整条件 ".repeat(120)]},
    degradation_policy: {small_scale_streak_threshold: 2, on_degradation: "require_blocker_or_expand_next_batch"},
  };
  source.execution_profile = profile;
  const original = structuredClone(source);
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const projected = (envelope.contract_capsule as JsonObject).execution_profile;
  assert.deepEqual(projected, profile);
  const signature = turnEnvelopeActionSignatureDocument(envelope);
  for (const mutate of [
    (value: JsonObject) => {value.minimum_scale = "any_operation";},
    (value: JsonObject) => {value.must_include = [];},
    (value: JsonObject) => {(value.outcome_floor as JsonObject).must_advance = [];},
    (value: JsonObject) => {delete value.degradation_policy;},
  ]) {
    const changed = structuredClone(envelope);
    mutate((changed.contract_capsule as JsonObject).execution_profile as JsonObject);
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed), signature);
  }
  assert.deepEqual(source, original);
  assert.equal((buildTurnEnvelope({payload: payload(),
    scheduler_execution_args: ""}).contract_capsule as JsonObject).execution_profile, undefined);
});

test("budget pressure retains complete independent planning context", () => {
  const source = payload();
  const context = {
    schema_version: "loopx_agent_context_v0", phase: "before_plan",
    scope: {goal_id: source.goal_id, agent_id: "agent-ts", todo_id: "todo-1"},
    target: "coordinator", authority: "guidance_only", delivery: "projected",
    contributions: [{hook_id: "fixture.planning", capability_id: "fixture-planning",
      revision: "1", guidance: ["Inspect original evidence.\nKeep the complete acceptance tail."],
      facts: {live_availability: "not_observed"}, source_refs: ["fixture:evidence"]}],
    failures: [],
  };
  (source.interaction_contract as JsonObject).agent_context = context;
  source.goal_boundary = {execution_profile: {padding: "x".repeat(8_000)}};
  const original = structuredClone(source);
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  assert.deepEqual(envelope.agent_context, context);
  assert.equal((envelope.agent_context as JsonObject).detail_ref, undefined);
  assert.equal((envelope.compaction as JsonObject).within_budget, false);
  assert.deepEqual(quotaActionSignatureDocument(source),
    turnEnvelopeActionSignatureDocument(envelope));
  const signature = structuredClone(turnEnvelopeActionSignatureDocument(envelope));
  ((envelope.agent_context as JsonObject).contributions as JsonObject[])[0].guidance = ["Skip evidence."];
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), signature);
  assert.deepEqual(source, original);
});

test("vision closeout obligations survive projection and signature mutation", () => {
  const source = payload();
  const audit = {
    schema_version: "vision_continuation_audit_v0", required: true,
    decision: "acceptance_gap_open", selected_todo_is_goal_completion: false,
    closeout_allowed_without_evidence: false,
    required_before_closeout: [
      "inspect registered material before public research",
      "preserve the original source and acceptance evidence " + "完整要求🧭 ".repeat(40),
      "derive requirements", "record evidence", "check current authority",
      "retain unsuccessful results", "continue when acceptance is unproven",
    ],
    recommended_action: "Inspect the original evidence first.\nIf acceptance remains unproven, " +
      "keep the vision active and write a successor or replan trigger.",
    authoritative_evidence_kinds: ["evaluation_outputs", "successor_state"],
    not_satisfied_by: ["todo_completion_alone", "vision_checkpoint_alone"],
    vision_gap_judge: {
      schema_version: "vision_gap_judge_v0", done: false, decision: "continue",
      reason: "Evidence remains incomplete.", done_only_when: [],
      continue_when: ["evidence_is_missing_weak_or_stale"], otherwise: "continue",
    },
    acceptance_gaps: [{private_detail: "read from the original capture"}],
  };
  source.vision_continuation_audit = audit;
  const before = structuredClone(source);
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: "", captured_decision_path: "private capture/decision.json"});
  const projected = (envelope.contract_capsule as JsonObject).vision_continuation_audit as JsonObject;
  for (const field of ["required_before_closeout", "recommended_action", "vision_gap_judge",
    "authoritative_evidence_kinds", "not_satisfied_by"] as const) {
    assert.deepEqual(projected[field], audit[field]);
  }
  assert.equal(projected.acceptance_gaps, undefined);
  assert.equal((envelope.detail_ref as JsonObject).full_decision, "cat -- 'private capture/decision.json'");
  assert.deepEqual(source, before);
  assert.deepEqual(quotaActionSignatureDocument(source),
    turnEnvelopeActionSignatureDocument(envelope));
  for (const mutate of [
    (value: JsonObject) => { (value.required_before_closeout as string[]).pop(); },
    (value: JsonObject) => { (value.required_before_closeout as string[]).reverse(); },
    (value: JsonObject) => { value.recommended_action = "close the vision"; },
    (value: JsonObject) => { (value.vision_gap_judge as JsonObject).done = true; },
    (value: JsonObject) => { value.not_satisfied_by = []; },
  ]) {
    const changed = structuredClone(envelope);
    mutate((changed.contract_capsule as JsonObject).vision_continuation_audit as JsonObject);
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed),
      turnEnvelopeActionSignatureDocument(envelope));
  }
  (projected.required_before_closeout as string[]).push("consumer edit");
  assert.deepEqual(source, before);
});

test("absent vision audit retains the existing envelope shape", () => {
  const source = payload();
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  assert.equal((envelope.contract_capsule as JsonObject).vision_continuation_audit, undefined);
});

test("capability facts retain exact current and historical source fields", () => {
  for (const fields of [
    {required: ["network", "filesystem_write"], missing: ["network"]},
    {required: ["network"], missing: []},
    {required_capabilities: ["network"], missing_capabilities: ["network"]},
  ]) {
    const source = payload();
    source.capability_gate = {action: "repair_bridge", reason: "Unavailable capability",
      ...fields, runnable_candidates: [{private_detail: "not in compact context"}], available: ["shell"]};
    const before = structuredClone(source);
    const envelope = buildTurnEnvelope({payload: source,
      scheduler_execution_args: ""});
    assert.deepEqual((envelope.boundary as JsonObject).capability_gate,
      {action: "repair_bridge", reason: "Unavailable capability", ...fields});
    assert.deepEqual(source, before);
    assert.deepEqual(quotaActionSignatureDocument(source),
      turnEnvelopeActionSignatureDocument(envelope));
    const changed = structuredClone(envelope);
    ((changed.boundary as JsonObject).capability_gate as JsonObject)[Object.keys(fields)[1]] = ["different"];
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed), turnEnvelopeActionSignatureDocument(envelope));
  }
});

test("optional memory participation is compact, verified and signed", () => {
  const plain = payload();
  const build = (source: JsonObject) => buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  const ordinary = build(plain);
  const memory = {enabled: true, configured_for_agent: true, experiment_available: true,
    automatic_recall: true, automatic_ingest: false, config_path: "private-config",
    enablement_receipt: {diagnostic: "not-host-context"}};
  for (const flag of ["enabled", "configured_for_agent", "experiment_available"]) {
    const source = payload();
    (source.goal_boundary as JsonObject).capabilities = {reward_memory: {...memory, [flag]: false}};
    assert.deepEqual(build(source).boundary, ordinary.boundary);
  }
  for (const [recall, ingest] of [[true, false], [false, true], [true, true], [false, false]]) {
    const source = payload();
    (source.goal_boundary as JsonObject).capabilities = {reward_memory: {...memory,
      automatic_recall: recall, automatic_ingest: ingest}};
    const envelope = build(source);
    const projected = (envelope.boundary as JsonObject).capabilities;
    assert.deepEqual(projected, recall || ingest ? {reward_memory: {
      automatic_recall: recall, automatic_ingest: ingest,
    }} : undefined);
    assert.deepEqual(quotaActionSignatureDocument(source),
      turnEnvelopeActionSignatureDocument(envelope));
    if (recall || ingest) {
      const signed = JSON.parse(JSON.stringify(turnEnvelopeActionSignatureDocument(envelope)));
      ((projected as JsonObject).reward_memory as JsonObject).automatic_ingest = !ingest;
      assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), signed);
    }
  }
});

test("settlement-only replans preserve owed commands and do not demand another outcome", () => {
  const prefix = "loopx --runtime-root /" + "long-path/".repeat(50);
  for (const commands of [
    [prefix + " refresh-state --goal-id goal-turn-envelope --turn-instance-id turn-original",
      prefix + " quota spend-slot --goal-id goal-turn-envelope --turn-instance-id turn-original --execute"],
    [prefix + " quota spend-slot --goal-id goal-turn-envelope --turn-instance-id turn-original --execute"],
  ]) {
    const source = payload();
    const interaction = source.interaction_contract as JsonObject;
    (interaction.cli_channel as JsonObject).next_cli_actions = commands;
    (interaction.agent_channel as JsonObject).primary_action = "Finish only the original Turn settlement; do not execute its successor.";
    source.replan_action_packet = {schema_version: "replan_action_packet_v0",
      obligation_id: "replan-1111111111111111", settlement_only: true,
      successor_todo_id: "todo_independent_successor", decision: "settlement_pending",
      writeback_contract: {rule: "Finish only the original Turn settlement; do not execute its successor."}};
    const envelope = buildTurnEnvelope({payload: source,  scheduler_execution_args: ""});
    assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, commands);
    assert.equal((envelope.replan_action_packet as JsonObject).settlement_only, true);
    assert.match(String((envelope.action as JsonObject).recommended_action), /original Turn settlement/);
    assert.equal((envelope.action as JsonObject).primary_action, (interaction.agent_channel as JsonObject).primary_action);
    assert.deepEqual(quotaActionSignatureDocument(source), turnEnvelopeActionSignatureDocument(envelope));
  }
});

test("a projected successor preserves its original closeout guard in the compact envelope", () => {
  const source = payload();
  const successor = "loopx todo add --goal-id goal-turn-envelope --replan-obligation-id replan-1111111111111111";
  const guard = "loopx --runtime-root /" + "long-path/".repeat(50) +
    " quota should-run --codex-app --goal-id goal-turn-envelope --turn-instance-id turn-original";
  const interaction = source.interaction_contract as JsonObject;
  (interaction.cli_channel as JsonObject).next_cli_actions = ["execute replan_action_packet.writeback_contract.successor_command", guard,
    "finish only the original Turn before ending; do not execute its successor"];
  source.replan_action_packet = {schema_version: "replan_action_packet_v0",
    obligation_id: "replan-1111111111111111", writeback_contract: {successor_command: successor}};
  const envelope = buildTurnEnvelope({payload: source,  scheduler_execution_args: ""});
  assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, [successor, guard]);
  assert.deepEqual(quotaActionSignatureDocument(source), turnEnvelopeActionSignatureDocument(envelope));
});

test("receipt-bound settlement recovery transports the original executable command intact", () => {
  const source = payload();
  const command = "loopx --runtime-root /" + "long-path/".repeat(50) +
    " quota spend-slot --goal-id goal-turn-envelope --agent-id agent-ts --todo-id todo-1" +
    " --turn-instance-id turn-original --execute";
  const interaction = source.interaction_contract as JsonObject;
  Object.assign(interaction.agent_channel as JsonObject, {delivery_allowed: false});
  Object.assign(interaction.cli_channel as JsonObject, {next_cli_actions: [command], spend_allowed_now: true});
  source.unsettled_host_turn_recovery = {schema_version: "unsettled_host_turn_recovery_v0",
    scope: "current_turn", repair: "quota_spend", binding_kind: "todo", binding_id: "todo-1",
    turn_instance_id: "turn-original"};
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  assert.equal((envelope.action as JsonObject).delivery_allowed, false);
  assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, [command]);
  assert.deepEqual(quotaActionSignatureDocument(source), turnEnvelopeActionSignatureDocument(envelope));
});

test("large peer inventories retain scoped gates and signed detail without hiding tasks", () => {
  const source = payload();
  const items = Array.from({ length: 50 }, (_, i) => ({ todo_id: `todo_${i}`,
    claimed_by: "worker", status: "open", task_class: "advancement_task",
    title: "Inspect independent source and return verified evidence" }));
  for (const available of [[], ["peer_agent_activation"]]) {
    const peer = projectPeerOrchestration({ agent_id: "parent", registered_agents: ["worker"],
      items, available_capabilities: available, agents: [{ agent_id: "worker", state: "running" }] })!;
    for (const nested of [false, true]) {
      source.task_orchestration_contract = nested
        ? { mode: "adaptive", execution_state: "ready", eligible_child_lanes: [{ todo_id: "local-child" }], peer_activation_diagnostic: peer }
        : peer;
      const render = () => buildTurnEnvelope({ payload: source,
         scheduler_execution_args: " --available-capability shell" });
      const envelope = render();
      const contract = envelope.task_orchestration_contract as JsonObject;
      const compact = (nested ? contract.peer_activation_diagnostic : contract) as JsonObject;
      assert.equal(compact.execution_scope, "peer_agent_activation");
      assert.equal(compact.execution_state, available.length ? "ready" : "blocked");
      assert.equal(compact.activation_allowed, available.length > 0);
      assert.equal(Number(compact.eligible_peer_count) + Number(compact.blocked_peer_count), 50);
      assert.equal(compact.read_required, true);
      assert.equal(compact.eligible_peer_lanes, undefined);
      assert.equal((peer.eligible_peer_lanes as unknown[]).length + (peer.blocked_peer_lanes as unknown[]).length, 50);
      assert.equal((envelope.compaction as JsonObject).within_budget, true);
      assert.deepEqual(quotaActionSignatureDocument(source), turnEnvelopeActionSignatureDocument(envelope));
      const rows = (available.length ? peer.eligible_peer_lanes : peer.blocked_peer_lanes) as JsonObject[];
      rows[0].todo_id = `changed-identity-${nested}`;
      assert.notEqual((render().action_signature as JsonObject).source_hash, (envelope.action_signature as JsonObject).source_hash);
      if (nested) assert.deepEqual(contract.eligible_child_lanes, [{ todo_id: "local-child" }]);
    }
  }
});

test("Turn preserves checkpointed scope approval without lifting other gates", () => {
  const source = payload();
  const scope = source.goal_boundary as Record<string, unknown>;
  scope.requires_parent_approval = ["write", "publish", "production-action"];
  const render = () => buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  const baseline = render();
  scope.checkpointed_boundary_authority = {
    schema_version: "checkpointed_boundary_authority_v0", active_count: 1,
    active_write_scope: ["src/**"], entries: [{source: "operator-decision"}],
  };
  const approved = render();
  const boundary = approved.boundary as Record<string, unknown>;
  assert.deepEqual(boundary.checkpointed_boundary_authority, {
    schema_version: "checkpointed_boundary_authority_v0", active_count: 1,
    active_write_scope: ["src/**"],
  });
  assert.deepEqual(boundary.requires_parent_approval, ["write", "publish", "production-action"]);
  for (const inactive of [
    {schema_version: "checkpointed_boundary_authority_v0", active_count: 0, active_write_scope: []},
    {schema_version: "unknown", active_count: 1, active_write_scope: ["**"]},
    {schema_version: "checkpointed_boundary_authority_v0", active_count: 1, active_write_scope: [null, ""]},
  ]) {
    scope.checkpointed_boundary_authority = inactive;
    assert.deepEqual(render().boundary, baseline.boundary);
  }
  delete scope.checkpointed_boundary_authority;
  assert.deepEqual(render(), baseline);
});

test("boundary obligations remain complete literals beyond display budgets", () => {
  const source = payload();
  const scopes = [...Array.from({length: 19}, (_, i) => `public/surface/${i}/**`),
    `public/${"nested/".repeat(30)}two  spaces/**`, "tests/**"];
  const boundary = source.goal_boundary as JsonObject;
  Object.assign(boundary, {
    write_scope: scopes,
    available_capabilities: Array.from({length: 20}, (_, i) => `capability_${i}`),
    requires_parent_approval: Array.from({length: 20}, (_, i) => `action_${i}`),
    guards: [...Array.from({length: 9}, (_, i) => `Preserve evidence ${i}`),
      "Keep the complete original evidence.\n".repeat(12) + "Stop before publishing private material."],
    stop_condition: "Read the full original source.\n".repeat(14) + "Stop when owner approval is missing.",
    checkpointed_boundary_authority: {
      schema_version: "checkpointed_boundary_authority_v0", active_count: 2,
      active_write_scope: scopes, entries: [{source: "private-decision-evidence"}],
    },
  });
  const original = structuredClone(source);
  const envelope = buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  const projected = envelope.boundary as JsonObject;
  for (const field of ["write_scope", "available_capabilities", "requires_parent_approval", "guards", "stop_condition"]) {
    assert.deepEqual(projected[field], boundary[field], field);
    const changedSource = structuredClone(source);
    const changed = changedSource.goal_boundary as JsonObject;
    if (Array.isArray(changed[field])) (changed[field] as unknown[]).pop();
    else changed[field] = String(changed[field]).replace("Stop when owner approval is missing.", "Continue.");
    assert.notDeepEqual(quotaActionSignatureDocument(changedSource),
      turnEnvelopeActionSignatureDocument(envelope), field);
  }
  const approval = projected.checkpointed_boundary_authority as JsonObject;
  assert.deepEqual(approval.active_write_scope, scopes);
  assert.equal(approval.entries, undefined);
  assert.deepEqual(source, original);
  (projected.write_scope as string[]).pop();
  assert.deepEqual(source, original);
});

test("explicit empty boundary lists remain distinguishable from absent facts", () => {
  const source = payload();
  source.goal_boundary = {write_scope: [], available_capabilities: [], requires_parent_approval: [], guards: [], stop_condition: ""};
  const envelope = buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  assert.deepEqual(envelope.boundary, {rule: "stay_in_scope_or_stop", ...source.goal_boundary as JsonObject});
});

test("required commands remain intact independently of additive hook prompt budgets", () => {
  const source = payload();
  const baseline = buildTurnEnvelope({ payload: source,  scheduler_execution_args: "" });
  const command = "loopx inspect --registry /" + "route/".repeat(80) + "registry.json";
  const read = { kind: "fixture_read", command, reason: "Read the pending observation",
    source: "turn_start_capability_hook" };
  source.required_reads = [read];
  const ordinary = buildTurnEnvelope({ payload: source,  scheduler_execution_args: "" });
  assert.equal((ordinary.compaction as JsonObject).budget_bytes, 8_192);
  assert.equal((ordinary.required_reads as JsonObject[])[0].command, command);
  assert.equal((ordinary.compaction as JsonObject).hook_prompt_budget_bytes, undefined);
  source.required_reads = [{ ...read, prompt_budget_bytes: 1_536 }];
  const active = buildTurnEnvelope({ payload: source,  scheduler_execution_args: "" });
  assert.equal((active.required_reads as JsonObject[])[0].command, command);
  assert.equal((active.compaction as JsonObject).budget_bytes, 8_192 + 1_536);
  assert.equal((active.compaction as JsonObject).hook_prompt_budget_bytes, 1_536);
  assert.equal((active.compaction as JsonObject).envelope_utf8_bytes, Buffer.byteLength(JSON.stringify(active)));
  for (const field of ["action", "user", "scheduler", "execution_policy", "writeback"]) {
    assert.deepEqual(active[field], baseline[field]);
  }
  source.required_reads = [{ ...read, source: "other", prompt_budget_bytes: 1_536 }];
  const unrelated = buildTurnEnvelope({ payload: source,  scheduler_execution_args: "" });
  assert.equal((unrelated.compaction as JsonObject).budget_bytes, 8_192);
  delete source.required_reads;
  assert.deepEqual(buildTurnEnvelope({ payload: source,  scheduler_execution_args: "" }), baseline);
});

test("pending capability action outranks stale replan commands and remains signed", () => {
  const source = payload();
  const command = "loopx periodic-report consume-pending --goal-id goal-turn-envelope --agent-id agent-ts --execute";
  source.effective_action = "governed_capability_intent";
  source.pending_capability_intent = {
    schema_version: "pending_capability_intent_projection_v0",
    capability_id: "periodic-report", intent_kind: "periodic_report.trigger_evaluation",
    idempotency_key: "periodic-report:fixture", intent_digest: "sha256:" + "a".repeat(64),
    goal_id: "goal-turn-envelope", agent_id: "agent-ts", state: "pending",
    action_kind: "consume_periodic_report_intent", action_summary: "Prepare one report",
    command, generation_authorized: true, external_delivery_authorized: true,
    agent_read_required: true,
  };
  source.replan_action_packet = {
    schema_version: "fixture-replan", decision: "replan",
    writeback_contract: { successor_command: "loopx todo add --goal-id goal-turn-envelope" },
  };
  const envelope = buildTurnEnvelope({payload: source,  scheduler_execution_args: ""});
  assert.equal(envelope.replan_action_packet, null);
  assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, [command]);
  assert.equal((envelope.required_reads as JsonObject[]).some(read => read.source === "selected_todo"), false);
  const signed = JSON.parse(JSON.stringify(turnEnvelopeActionSignatureDocument(envelope)));
  assert.deepEqual((signed.action as JsonObject).capability_intent, source.pending_capability_intent);
  (source.pending_capability_intent as JsonObject).command = "untrusted replacement";
  assert.throws(() => buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""}), /action is unsupported/);
});

test("Turn envelope transaction owns compaction and signature construction", () => {
  const source = payload();
  const envelope = buildTurnEnvelope({
    payload: source,

    scheduler_execution_args: " --scheduler-runtime-profile codex_app",
  });

  assert.equal(envelope.schema_version, "loopx_turn_envelope_v0");
  assert.equal(envelope.agent_id, "agent-ts");
  assert.equal(
    (envelope.detail_ref as Record<string, unknown>).full_decision,
    "loopx --format json quota should-run --goal-id goal-turn-envelope " +
      "--agent-id agent-ts --scheduler-runtime-profile codex_app",
  );
  const capsule = envelope.contract_capsule as Record<string, unknown>;
  assert.equal(capsule.protocol_action_packet, undefined);
  const signature = envelope.action_signature as Record<string, unknown>;
  assert.equal(signature.coverage, ACTION_SIGNATURE_COVERAGE_V0);
  assert.equal(signature.matches, true);
  assert.equal(
    signature.envelope_hash,
    (signature.source_hash as string),
  );
  assert.equal(
    (envelope.compaction as Record<string, unknown>).within_budget,
    true,
  );
});

test("Trae App Turn envelope preserves app automation without a Codex alias", () => {
  const source = payload();
  source.scheduler_hint = {
    action: "run_now",
    cadence_class: "active_work",
    app_automation: {
      host_surface: "trae_app",
      apply: "update_automation_cadence_if_possible",
      recommended_rrule: "FREQ=MINUTELY;INTERVAL=3",
      stateful_backoff: {
        state_key: "scheduler_hint.app_automation.stateful_backoff",
        current_rrule: "FREQ=MINUTELY;INTERVAL=15",
        apply_needed: true,
        ack_needed: false,
        state_status: "reset_required",
      },
      ack_hint: {
        cli_args: [
          "quota", "scheduler-ack-current", "--surface", "trae_app",
          "--execute",
        ],
      },
      failure_hint: {
        cli_args: [
          "quota", "scheduler-fail-current", "--surface", "trae_app",
          "--execute",
        ],
      },
    },
  };

  const envelope = buildTurnEnvelope({
    payload: source,

    scheduler_execution_args: " --scheduler-runtime-profile trae_app",
  });
  const scheduler = envelope.scheduler as Record<string, unknown>;
  const app = scheduler.app_automation as Record<string, unknown>;

  assert.equal(scheduler.codex_app, undefined);
  assert.equal(app.host_surface, "trae_app");
  assert.deepEqual(app.ack_cli_args, [
    "quota", "scheduler-ack-current", "--surface", "trae_app",
    "--execute",
  ]);
  assert.equal(
    (app.stateful_backoff as Record<string, unknown>).state_key,
    "scheduler_hint.app_automation.stateful_backoff",
  );
  assert.deepEqual(app.failure_cli_args_detail_ref, {
    reason: "cold_path_until_host_update_failure",
    detail_ref: "full_decision.scheduler_hint.app_automation.failure_hint.cli_args",
  });
});

test("scheduler omitted argv resolves the captured source carrier, never a new quota command", () => {
  for (const carrier of ["app_automation", "codex_app"]) {
    const source = payload();
    const ack = ["quota", "scheduler-ack-current", "--registry", "r".repeat(513)];
    const failure = ["quota", "scheduler-fail-current", "--execute"];
    source.scheduler_hint = {action: "run_now", [carrier]: {
      host_surface: "codex_app", ack_hint: {cli_args: ack}, failure_hint: {cli_args: failure},
    }};
    if (carrier === "app_automation") {
      (source.scheduler_hint as JsonObject).codex_app = {ack_hint: {cli_args: ["different-alias"]}};
    }
    const before = structuredClone(source);
    const envelope = buildTurnEnvelope({payload: source,
      scheduler_execution_args: " --codex-app", captured_decision_path: "/tmp/capture/decision.json"});
    const app = (envelope.scheduler as JsonObject)[carrier] as JsonObject;
    if (carrier === "app_automation") assert.deepEqual((envelope.scheduler as JsonObject).codex_app, app);
    assert.equal(app.ack_cli_args, undefined);
    for (const [field, expected] of [["ack", ack], ["failure", failure]] as const) {
      const ref = app[`${field}_cli_args_detail_ref`] as JsonObject;
      assert.equal(ref.request, undefined);
      assert.equal(ref.detail_ref, `full_decision.scheduler_hint.${carrier}.${field}_hint.cli_args`);
      const resolved = String(ref.detail_ref).split(".").slice(1)
        .reduce<unknown>((value, key) => (value as JsonObject)[key], source);
      assert.deepEqual(resolved, expected);
    }
    assert.equal((envelope.detail_ref as JsonObject).full_decision, "cat -- /tmp/capture/decision.json");
    assert.deepEqual(source, before);
    assert.deepEqual(quotaActionSignatureDocument(source),
      turnEnvelopeActionSignatureDocument(envelope));
    const changed = structuredClone(envelope);
    ((((changed.scheduler as JsonObject)[carrier] as JsonObject).ack_cli_args_detail_ref) as JsonObject)
      .detail_ref = "full_decision.scheduler_hint.wrong.ack_hint.cli_args";
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed), turnEnvelopeActionSignatureDocument(envelope));
  }
});

test("monitor-only capsule preserves the non-runnable non-monitor count", () => {
  const source = payload();
  source.should_run = false;
  source.effective_action = "monitor_quiet_skip";
  source.interaction_contract = {
    schema_version: "loopx_interaction_contract_v0",
    mode: "monitor_quiet_until_material_transition",
    user_channel: { action_required: false, notify: "DONT_NOTIFY" },
    agent_channel: {
      must_attempt: false,
      delivery_allowed: false,
      quiet_noop_allowed: true,
    },
    cli_channel: {
      next_cli_actions: [],
      spend_allowed_now: false,
      spend_after_validation: false,
    },
  };
  source.work_lane_contract = {
    schema_version: "work_lane_contract_v0",
    lane: "continuous_monitor",
    obligation: "quiet_until_material_monitor_transition",
    must_attempt_work: false,
    reason_codes: ["non_runnable_non_monitor_todos_present"],
    non_runnable_non_monitor_count: 2,
  };

  const envelope = buildTurnEnvelope({
    payload: source,
    scheduler_execution_args: "",
  });
  const capsule = envelope.contract_capsule as Record<string, unknown>;

  assert.deepEqual(capsule.work_lane_contract, source.work_lane_contract);
});

test("planning detail stays cold while its action dimension remains signed", () => {
  const source = payload();
  source.planning_horizon = {
    schema_version: "quota_planning_horizon_v0",
    horizon: "near",
    detail_refs: { candidate: "$.candidate" },
  };
  const interaction = source.interaction_contract as Record<string, unknown>;
  const action = interaction.agent_channel as Record<string, unknown>;
  action.primary_action = "advance the near horizon";

  const signature = quotaActionSignatureDocument(source);
  assert.equal(signature.coverage, ACTION_SIGNATURE_COVERAGE_V3);
  const horizon = (signature.action as Record<string, unknown>)
    .planning_horizon as Record<string, unknown>;
  assert.equal(horizon.detail_refs, undefined);
  assert.equal(horizon.detail_refs_ref, "$.detail_ref");
});

test("Goal horizon reuses only identical inline acceptance and keeps its source signed", () => {
  const source = payload();
  const acceptance = {revision: 3, scope: {kind: "all_advancement"}, status: "failed",
    verification: {results: [{criterion_id: "complete", passed: false}]}};
  source.planning_horizon = {schema_version: "quota_planning_horizon_v0", core_goal: {
    objective: "Deliver the whole outcome", acceptance_contract: acceptance}};
  const channel = (source.interaction_contract as JsonObject).agent_channel as JsonObject;
  channel.work_context = {complete: true, sources: [{source: "goal_acceptance", content: {
    goal_acceptance_contract: acceptance}}]};
  const build = () => buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const envelope = build();
  const core = ((envelope.action as JsonObject).planning_horizon as JsonObject).core_goal as JsonObject;
  assert.equal(core.acceptance_contract, undefined);
  assert.equal(core.acceptance_contract_ref, "$.work_context.sources[0].content.goal_acceptance_contract");
  assert.deepEqual((source.planning_horizon as JsonObject).core_goal,
    {objective: "Deliver the whole outcome", acceptance_contract: acceptance}, "compaction must not mutate full quota");
  const signature = turnEnvelopeActionSignatureDocument(envelope);
  const compact = structuredClone(source);
  (compact.planning_horizon as JsonObject).core_goal = {
    objective: "Deliver the whole outcome",
    acceptance_contract_ref: "$.interaction_contract.agent_channel.work_context.sources[0].content.goal_acceptance_contract",
  };
  const fromCli = buildTurnEnvelope({payload: compact,
    scheduler_execution_args: ""});
  assert.deepEqual(turnEnvelopeActionSignatureDocument(fromCli), signature);
  const compactChannel = (compact.interaction_contract as JsonObject).agent_channel as JsonObject;
  for (const context of [{}, {sources: []}, {sources: [{source: "goal_acceptance", content: {}}]},
    {sources: [{source: "unrelated", content: {goal_acceptance_contract: acceptance}}]},
    {sources: [{source: "goal_acceptance", content: {status: "unavailable", goal_acceptance_contract: acceptance}}]}]) {
    compactChannel.work_context = context;
    assert.throws(() => buildTurnEnvelope({payload: compact,
      scheduler_execution_args: ""}), /Goal acceptance reference must resolve/);
  }
  const changed = structuredClone(envelope);
  const row = ((changed.work_context as JsonObject).sources as JsonObject[])[0];
  (row.content as JsonObject).goal_acceptance_contract = {...acceptance, status: "accepted"};
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed), signature);
  for (const content of [{goal_acceptance_contract: {...acceptance, revision: 4}}, {},
    {goal_acceptance_contract: {...acceptance, scope: {kind: "selected_work"}}}]) {
    channel.work_context = {sources: [{source: "goal_acceptance", content}]};
    const retained = ((build().action as JsonObject).planning_horizon as JsonObject).core_goal as JsonObject;
    assert.deepEqual(retained.acceptance_contract, acceptance);
    assert.equal(retained.acceptance_contract_ref, undefined);
  }
});

test("Goal horizon reuses identical selected work text without dropping other work or its bounds", () => {
  const source = payload();
  const text = "Deliver the complete candidate and check the independent acceptance before settling.";
  source.selected_todo = {...source.selected_todo as JsonObject, text};
  const horizon = {core_goal: {objective: "Finish the whole Goal"},
    work_items: [{todo_id: "todo-1", text}, {todo_id: "todo-2", text: "Run diagnostic validation"}],
    completeness: {complete: false, omitted_candidate_todo_count: 3}};
  source.planning_horizon = horizon;
  const build = () => buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const projected = () => (build().action as JsonObject).planning_horizon as JsonObject;
  assert.deepEqual(projected().work_items, [{todo_id: "todo-1", text_ref: "$.action.selected_todo.text"}, horizon.work_items[1]]);
  assert.deepEqual(projected().completeness, horizon.completeness);
  assert.equal(horizon.work_items[0].text, text, "full quota source must remain intact");
  source.recommended_action = text;
  assert.equal((projected().work_items as JsonObject[])[0].text_ref, "$.action.recommended_action");
  source.selected_todo = {...source.selected_todo as JsonObject, text: "Different work"};
  assert.equal((projected().work_items as JsonObject[])[0].text, text);
  source.planning_horizon = {work_items: horizon.work_items};
  assert.deepEqual(projected().work_items, horizon.work_items, "Goal opt-out keeps the existing transport");
});

test("v0 compaction metric preserves Unicode code-point compatibility", () => {
  const source = payload();
  source.reason = "推进一段 🚀";
  const envelope = buildTurnEnvelope({
    payload: source,

    scheduler_execution_args: "",
  });
  assert.equal(
    (envelope.compaction as Record<string, unknown>).source_json_bytes,
    [...JSON.stringify(source)].length,
  );
});

test("warning accounting converges across decimal-width and ratio boundaries", () => {
  assert.equal(Object.values(TURN_ENVELOPE_SECTION_TARGETS).reduce((a, b) => a + b, 0), 8192);
  for (let size = 6_000; size < 6_300; size += 1) {
    const source = payload();
    source.goal_boundary = { execution_profile: { padding: "界".repeat(size) } };
    const envelope = buildTurnEnvelope({ payload: source, scheduler_execution_args: "" });
    const metric = envelope.compaction as Record<string, any>;
    const bytes = Buffer.byteLength(JSON.stringify(envelope), "utf8");
    assert.equal(metric.envelope_utf8_bytes, bytes);
    assert.equal(metric.envelope_json_bytes, [...JSON.stringify(envelope)].length);
    assert.equal(Object.values(metric.warning.section_bytes as Record<string, number>).reduce((a, b) => a + b, 0), bytes);
    assert.equal(metric.warning.excess_bytes, bytes - 8192);
  }
});

test("signature key ordering preserves Python Unicode code-point compatibility", () => {
  const source = {
    goal_id: "g",
    agent_identity: { agent_id: "a" },
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      mode: "quiet",
      user_channel: { action_required: false, notify: "DONT_NOTIFY" },
      agent_channel: {
        must_attempt: false,
        delivery_allowed: false,
        quiet_noop_allowed: true,
      },
      cli_channel: {},
    },
    goal_boundary: {
      execution_profile: { "𐀀": "astral", "": "bmp" },
    },
  };
  const envelope = buildTurnEnvelope({
    payload: source,
    scheduler_execution_args: "",
  });
  assert.equal(
    (envelope.action_signature as Record<string, unknown>).source_decision_hash,
    "sha256:c68a197d600a6b89b6d2816a77e2de4c03e0cc6a7ac5821d039d9320ab341795",
  );
});

test("selected Todo text preserves case-sensitive requirements", () => {
  const source = payload();
  source.recommended_action = "STRASSE";
  (source.selected_todo as Record<string, unknown>).text = "Straße";
  const interaction = source.interaction_contract as Record<string, unknown>;
  (interaction.agent_channel as Record<string, unknown>).primary_action = "STRASSE";
  const envelope = buildTurnEnvelope({
    payload: source,

    scheduler_execution_args: "",
  });
  const selectedTodo = (envelope.action as Record<string, unknown>)
    .selected_todo as Record<string, unknown>;
  assert.equal(selectedTodo.text_ref, undefined);
  assert.equal(selectedTodo.text, "Straße");
});

test("selected work retains trailing requirements beyond every display limit", () => {
  const source = payload();
  const work = "Repair the cursor boundary. " + "Keep the existing query semantics. ".repeat(70) +
    "Acceptance: no omitted or duplicate records; add a regression and preserve cancellation.";
  source.recommended_action = work.slice(0, 477) + "...";
  (source.selected_todo as Record<string, unknown>).text = work;
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const selected = (envelope.action as Record<string, unknown>).selected_todo as Record<string, unknown>;
  assert.equal(selected.text, work);
  assert.equal(selected.text_ref, undefined);
  assert.equal((envelope.action_signature as Record<string, unknown>).matches, true);
  const signed = structuredClone(turnEnvelopeActionSignatureDocument(envelope));
  selected.text = work.replace("preserve cancellation", "ignore cancellation");
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), signed);
});

test("selected work aliases only an exact complete recommendation", () => {
  const source = payload();
  const work = "Preserve case-sensitive API names: CursorA and cursora.";
  source.recommended_action = work;
  (source.selected_todo as Record<string, unknown>).text = work;
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const selected = (envelope.action as Record<string, unknown>).selected_todo as Record<string, unknown>;
  assert.equal(selected.text_ref, "action.recommended_action");
  assert.equal(selected.text, undefined);
});

test("selected dependency keeps its original readiness and event proof signed", () => {
  const source = payload();
  const original = source.selected_todo as Record<string, unknown>;
  Object.assign(original, {
    resume_when: "pr_merged:owner/repo#42", resume_ready: true,
    resume_condition: {schema_version: "todo_resume_condition_v0", kind: "pr_merged",
      satisfied: true, matched_pr_ref: "owner/repo#42", matched_event_id: "original-merge",
      matched_event_at: "2026-10-01T00:00:00Z", source: "rollout_event_log"},
  });
  const envelope = buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const selected = (envelope.action as Record<string, unknown>).selected_todo as Record<string, unknown>;
  for (const field of ["resume_when", "resume_ready", "resume_condition"]) {
    assert.deepEqual(selected[field], original[field]);
  }
  const signed = structuredClone(turnEnvelopeActionSignatureDocument(envelope));
  selected.resume_ready = false;
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), signed);
  selected.resume_ready = true;
  (selected.resume_condition as Record<string, unknown>).matched_event_id = "another-event";
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), signed);
  const historical = buildTurnEnvelope({payload: payload(),
    scheduler_execution_args: ""});
  const historicalSelected = (historical.action as Record<string, unknown>).selected_todo as Record<string, unknown>;
  for (const field of ["resume_when", "resume_ready", "resume_condition"]) assert.equal(field in historicalSelected, false);
});

test("signature document fails closed to the canonical signed dimensions", () => {
  const envelope = buildTurnEnvelope({
    payload: payload(),

    scheduler_execution_args: "",
  });
  const first = structuredClone(turnEnvelopeActionSignatureDocument(envelope));
  envelope.detail_ref = { attacker_controlled: true };
  envelope.compaction = { attacker_controlled: true };
  const second = turnEnvelopeActionSignatureDocument(envelope);
  assert.deepEqual(second, first);

  (envelope.action as Record<string, unknown>).primary_action = "different action";
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), first);
});

test("unsupported facade operation is rejected", () => {
  assert.throws(
    () => evaluateTurnEnvelope({ operation: "leaf_helper" }),
    (error: unknown) =>
      error instanceof EffectRuntimeRequestError &&
      error.message === "turn envelope operation is unsupported",
  );
});

test("transaction boundary rejects malformed prepared facts", () => {
  assert.throws(
    () => buildTurnEnvelope({
      payload: payload(),
      scheduler_execution_args: [],
    }),
    EffectRuntimeRequestError,
  );
  const invalid = payload();
  invalid.open_count = "not-an-integer";
  assert.throws(
    () => buildTurnEnvelope({
      payload: invalid,
      scheduler_execution_args: "",
    }),
    EffectRuntimeRequestError,
  );

  for (const [field, invalidValue] of [
    ["goal_id", { injected: true }],
    ["runtime_root", ["unexpected"]],
  ] as const) {
    const malformed = payload();
    malformed[field] = invalidValue;
    assert.throws(
      () => buildTurnEnvelope({
        payload: malformed,
        scheduler_execution_args: "",
      }),
      EffectRuntimeRequestError,
    );
  }

  const malformedMode = payload();
  (malformedMode.interaction_contract as Record<string, unknown>).mode = {
    injected: true,
  };
  assert.throws(
    () => buildTurnEnvelope({
      payload: malformedMode,
      scheduler_execution_args: "",
    }),
    EffectRuntimeRequestError,
  );
});


test("required reads prefer the agent channel and retain obligation identity", () => {
  const source = payload();
  const read = {
    kind: "agent_preferences", command: "loopx inspect --fresh",
    source: "turn_start_capability_hook", ordering: "before_work",
    hook_id: "semantic_preference.agent_context", capability_id: "semantic-preference",
  };
  const agent = (source.interaction_contract as JsonObject).agent_channel as JsonObject;
  agent.required_reads = [read];
  (source.interaction_contract as JsonObject).required_reads = [{command: "old root read"}];
  source.required_reads = [{command: "old payload read"}];
  const render = () => buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  const result = render();
  assert.deepEqual(result.required_reads, [read]);
  const tampered = structuredClone(result);
  ((tampered.required_reads as JsonObject[])[0]).ordering = "after_work";
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(tampered),
    quotaActionSignatureDocument(source));
  agent.required_reads = [];
  assert.deepEqual(render().required_reads, []);
  delete agent.required_reads;
  assert.deepEqual(render().required_reads, [{command: "old root read"}]);
  delete (source.interaction_contract as JsonObject).required_reads;
  assert.deepEqual(render().required_reads, [{command: "old payload read"}]);
});

test("all required reads survive compaction and later reads affect the signature", () => {
  const source = payload();
  // The sixth read used to disappear; long quoted routes were also rewritten.
  const reads = Array.from({length: 12}, (_, index) => ({
    kind: index === 5 ? "agent_preferences" : `fixture_${index}`,
    command: `loopx --registry '/${"workspace  dir/".repeat(45)}registry.json' inspect --item ${index}`,
    source: "turn_start_capability_hook", reason: "Read before work",
    ...(index === 11 ? {prompt_budget_bytes: 1_536} : {}),
  }));
  (source.interaction_contract as JsonObject).required_reads = reads;
  const render = () => buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  const result = render();
  assert.deepEqual((result.required_reads as JsonObject[]).map(x => x.command), reads.map(x => x.command));
  assert.equal((result.compaction as JsonObject).budget_bytes, 8_192 + 1_536);
  assert.equal((result.compaction as JsonObject).within_budget, false);
  assert.equal((result.action_signature as JsonObject).matches, true);
  const tampered = structuredClone(result);
  (tampered.required_reads as JsonObject[]).pop();
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(tampered), quotaActionSignatureDocument(source));
  reads[5].command += " --fresh";
  assert.notEqual((render().action_signature as JsonObject).source_hash, (result.action_signature as JsonObject).source_hash);
});

test("required read ordering and hook identity survive legacy carriers without private diagnostics", () => {
  const source = payload();
  const read = {command: "loopx context read", ordering: "before_work",
    hook_id: "fixture.context", capability_id: "fixture",
    private_detail: "provider payload is not an execution fact"};
  source.required_reads = [read];
  const result = buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  assert.deepEqual(result.required_reads, [{command: read.command, ordering: read.ordering,
    hook_id: read.hook_id, capability_id: read.capability_id}]);
  assert.deepEqual(quotaActionSignatureDocument(source),
    turnEnvelopeActionSignatureDocument(result));
});


test("unavailable hook context is signed without suppressing independent work", () => {
  const source = payload();
  const render = () => buildTurnEnvelope({payload: source,
     scheduler_execution_args: ""});
  const baseline = render();
  source.turn_start_capability_hook_dispatch = {results: [], failures: []};
  assert.deepEqual(turnEnvelopeActionSignatureDocument(render()), turnEnvelopeActionSignatureDocument(baseline));
  for (const [status, field] of [["unavailable", "results"], ["partial", "results"], ["failed", "failures"]]) {
    const row = {hook_id: "fixture.context", capability_id: "fixture", status, error_code: "provider_failed", private_detail: "must not leak"};
    source.turn_start_capability_hook_dispatch = {[field]: [row]};
    const result = render();
    const missing = (result.contract_capsule as JsonObject).unavailable_context as JsonObject;
    assert.deepEqual(missing, {affected_hooks: [{hook_id: "fixture.context", capability_id: "fixture", status, error_code: "provider_failed"}],
      cache_policy: "invalidate_affected_hook_context", dependent_action_policy: "hold_until_fresh_context",
      independent_work_policy: "preserve_existing_authority"});
    for (const key of ["action", "boundary", "execution_policy", "writeback"]) assert.deepEqual(result[key], baseline[key]);
    assert.equal(JSON.stringify(result).includes("must not leak"), false);
    assert.equal((result.action_signature as JsonObject).matches, true);
    assert.notEqual((result.action_signature as JsonObject).source_hash, (baseline.action_signature as JsonObject).source_hash);
    missing.cache_policy = "reuse_cached_context";
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(result), quotaActionSignatureDocument(source));
  }
});


test("captured details reuse one observation without changing action or default projection", () => {
  const source = payload();
  source.heartbeat_receipt = { turn_instance_id: "turn-observed" };
  const request = { payload: source,  scheduler_execution_args: "" };
  const before = JSON.stringify(source);
  const original = buildTurnEnvelope(request);
  const captured = buildTurnEnvelope({ ...request, captured_decision_path: "/tmp/capture/decision.json" });
  assert.deepEqual(captured.action_signature, original.action_signature);
  assert.deepEqual(captured.writeback, original.writeback);
  assert.deepEqual(captured.action, original.action);
  const detail = captured.detail_ref as JsonObject;
  assert.equal(detail.full_decision, "cat -- /tmp/capture/decision.json");
  assert.equal((detail.captured_decision as JsonObject).turn_instance_id, "turn-observed");
  assert.match(String((detail.captured_decision as JsonObject).instruction), /no fresh authority/);
  assert.equal(detail.todo_detail, (original.detail_ref as JsonObject).todo_detail);
  assert.equal(JSON.stringify(source), before);
  assert.deepEqual(buildTurnEnvelope({ ...request, captured_decision_path: undefined }), original);
  assert.equal((original.detail_ref as JsonObject).captured_decision, undefined);
  for (const invalid of [null, false, 4, {}, [], "", " ", "\0bad"]) {
    assert.throws(() => buildTurnEnvelope({ ...request, captured_decision_path: invalid }), EffectRuntimeRequestError);
  }
});


test("envelope consumes the canonical channel read list and signs every source route", () => {
  const source = payload();
  source.registry = "/tmp/selected work/registry.json";
  source.runtime_root = "/tmp/selected work/runtime";
  (source.selected_todo as JsonObject).todo_id = "todo_exact_work";
  const interaction = source.interaction_contract as JsonObject;
  const sharedReads = [
    {command: "cat -- '/tmp/selected work/state.md'", source: "goal_state"},
    {command: "loopx --registry '/tmp/selected work/registry.json' --runtime-root '/tmp/selected work/runtime' --format json todo list --goal-id goal-turn-envelope --todo-id todo_exact_work", source: "selected_todo"},
  ];
  (interaction.agent_channel as JsonObject).required_reads = sharedReads;
  interaction.required_reads = [{command: "obsolete-fallback", source: "existing"}];
  source.required_reads = [{command: "payload-fallback", source: "existing"}];
  const build = () => buildTurnEnvelope({payload: source,
    scheduler_execution_args: ""});
  const envelope = build();
  const reads = envelope.required_reads as JsonObject[];
  assert.deepEqual(reads, sharedReads);
  const read = reads.find(item => item.source === "selected_todo")!;
  assert.equal(read.command, "loopx --registry '/tmp/selected work/registry.json' --runtime-root '/tmp/selected work/runtime' --format json todo list --goal-id goal-turn-envelope --todo-id todo_exact_work");
  assert.deepEqual(quotaActionSignatureDocument(source), turnEnvelopeActionSignatureDocument(envelope));
  const signed = structuredClone(turnEnvelopeActionSignatureDocument(envelope));
  read.command = String(read.command).replace("todo_exact_work", "todo_wrong_work");
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(envelope), signed);

  const goalTampered = structuredClone(envelope);
  (goalTampered.required_reads as JsonObject[])[0].command = "cat -- /tmp/wrong-goal.md";
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(goalTampered), signed);
  // Even an explicit empty canonical list takes precedence over historical fallbacks.
  (interaction.agent_channel as JsonObject).required_reads = [];
  assert.deepEqual(build().required_reads, []);
  delete (interaction.agent_channel as JsonObject).required_reads;
  assert.deepEqual(build().required_reads, interaction.required_reads);
  delete interaction.required_reads;
  assert.deepEqual(build().required_reads, source.required_reads);
});

test("envelope never independently invents requirement reads from work or admission", () => {
  for (const change of [
    (source: JsonObject) => {source.should_run = false;},
    (source: JsonObject) => {source.selected_todo = null;},
    (source: JsonObject) => {((source.interaction_contract as JsonObject).agent_channel as JsonObject).delivery_allowed = false;},
    (source: JsonObject) => {((source.interaction_contract as JsonObject).cli_channel as JsonObject).selection_required = true;},
    (source: JsonObject) => {source.replan_action_packet = {settlement_only: true};},
  ]) {
    const source = payload();
    change(source);
    const envelope = buildTurnEnvelope({payload: source,
      scheduler_execution_args: ""});
    assert.deepEqual(envelope.required_reads, []);
  }
});


test("native Turn capture binds the original observation without inventing admission", () => {
  const decision = payload();
  const envelope = buildTurnEnvelope({payload: decision,
    scheduler_execution_args: ""});
  const request = {operation: "bind_capture", payload: decision, envelope,
    turn_instance_id: "native-turn", turn_key: "sha256:" + "a".repeat(64),
    captured_decision_path: "/tmp/native-turn/decision.json"};
  const bound = evaluateTurnEnvelope(request);
  assert.deepEqual(bound.action_signature, envelope.action_signature);
  const capture = (bound.detail_ref as JsonObject).captured_decision as JsonObject;
  assert.equal(capture.turn_instance_id, "native-turn");
  assert.equal(capture.turn_key, request.turn_key);
  assert.equal(decision.heartbeat_receipt, undefined);
  assert.equal((bound.detail_ref as JsonObject).full_decision, "cat -- /tmp/native-turn/decision.json");
  assert.deepEqual(evaluateTurnEnvelope({...request, envelope: bound}), bound);
  for (const turn_key of ["a".repeat(64), "sha256:" + "A".repeat(64),
      "sha256:" + "a".repeat(63), "sha256:" + "a".repeat(65), "sha256:" + "g".repeat(64)]) {
    assert.throws(() => evaluateTurnEnvelope({...request, turn_key}),
      /Turn decision capture does not match the original observation/);
  }
  for (const change of [{reason: "new source"}, {goal_id: "other"},
    {agent_identity: {agent_id: "other"}}, {ok: false}]) {
    assert.throws(() => evaluateTurnEnvelope({...request, payload: {...decision, ...change}}),
      EffectRuntimeRequestError);
  }
  for (const change of [{turn_instance_id: "other-turn"}, {turn_key: "sha256:" + "b".repeat(64)},
    {captured_decision_path: "/tmp/other.json"}]) {
    assert.throws(() => evaluateTurnEnvelope({...request, envelope: bound, ...change}),
      EffectRuntimeRequestError);
  }
});


test("consumer version gate rejects explicit future/null declarations without rewriting v0", () => {
  const envelope = buildTurnEnvelope({payload: payload(), scheduler_execution_args: ""});
  const before = structuredClone(envelope);
  assertTurnEnvelopeConsumerVersion(envelope);
  for (const field of ["envelope", "signature", "coverage"]) {
    for (const unsupported of [null, "future-required-semantics", "", 1]) {
      const changed = structuredClone(envelope);
      const carrier = field === "envelope" ? changed : changed.action_signature as JsonObject;
      carrier[field === "coverage" ? "coverage" : "schema_version"] = unsupported;
      assert.throws(() => assertTurnEnvelopeConsumerVersion(changed), /unsupported TurnEnvelope/);
    }
  }
  // Preserve the existing v0 planning transport; this gate neither upgrades
  // historical objects nor substitutes for the caller's digest verification.
  const legacy = structuredClone(envelope);
  delete (legacy.action_signature as JsonObject).schema_version;
  delete (legacy.action_signature as JsonObject).coverage;
  assertTurnEnvelopeConsumerVersion(legacy);
  assert.deepEqual(envelope, before);
});
