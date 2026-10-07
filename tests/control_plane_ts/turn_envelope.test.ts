import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTION_SIGNATURE_COVERAGE_V0,
  ACTION_SIGNATURE_COVERAGE_V3,
  buildTurnEnvelope,
  evaluateTurnEnvelope,
  quotaActionSignatureDocument,
  turnEnvelopeActionSignatureDocument,
} from "../../loopx/control_plane/quota/turn_envelope.ts";
import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { TURN_ENVELOPE_SECTION_TARGETS } from "../../loopx/control_plane/quota/turn_envelope_budget.ts";
import { projectPeerOrchestration } from "../../loopx/control_plane/quota/peer_orchestration.ts";

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
    protocol_action_packet: {
      schema_version: "protocol_action_packet_v0",
      summary: "actor=agent user_action_required=false agent_action_required=true " +
        "quiet_noop_allowed=false llm=no_api agent_action=advance one bounded segment",
    },
    work_lane_contract: {
      schema_version: "work_lane_contract_v0",
      must_attempt_work: true,
    },
    goal_boundary: { rule: "stay_in_scope_or_stop", write_scope: ["loopx/**"] },
  };
}

const protocolActionFields = {
  actor: "agent",
  user_action_required: false,
  agent_action_required: true,
  quiet_noop_allowed: false,
  llm: "no_api",
  agent_action: "advance one bounded segment",
};

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
    const envelope = buildTurnEnvelope({payload: source, protocol_action_fields: protocolActionFields,
      scheduler_execution_args: ""});
    assert.deepEqual((envelope.boundary as JsonObject).capability_gate,
      {action: "repair_bridge", reason: "Unavailable capability", ...fields});
    assert.deepEqual(source, before);
    assert.deepEqual(quotaActionSignatureDocument(source, protocolActionFields),
      turnEnvelopeActionSignatureDocument(envelope));
    const changed = structuredClone(envelope);
    ((changed.boundary as JsonObject).capability_gate as JsonObject)[Object.keys(fields)[1]] = ["different"];
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(changed), turnEnvelopeActionSignatureDocument(envelope));
  }
});

test("optional memory participation is compact, verified and signed", () => {
  const plain = payload();
  const build = (source: JsonObject) => buildTurnEnvelope({payload: source,
    protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
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
    assert.deepEqual(quotaActionSignatureDocument(source, protocolActionFields),
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
    const envelope = buildTurnEnvelope({payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
    assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, commands);
    assert.equal((envelope.replan_action_packet as JsonObject).settlement_only, true);
    assert.match(String((envelope.action as JsonObject).recommended_action), /original Turn settlement/);
    assert.equal((envelope.action as JsonObject).primary_action, (interaction.agent_channel as JsonObject).primary_action);
    assert.deepEqual(quotaActionSignatureDocument(source, protocolActionFields), turnEnvelopeActionSignatureDocument(envelope));
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
  const envelope = buildTurnEnvelope({payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
  assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, [successor, guard]);
  assert.deepEqual(quotaActionSignatureDocument(source, protocolActionFields), turnEnvelopeActionSignatureDocument(envelope));
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
        protocol_action_fields: protocolActionFields, scheduler_execution_args: " --available-capability shell" });
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
      assert.deepEqual(quotaActionSignatureDocument(source, protocolActionFields), turnEnvelopeActionSignatureDocument(envelope));
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
    protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
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
    {schema_version: "checkpointed_boundary_authority_v0", active_count: 1, active_write_scope: ["x".repeat(181)]},
  ]) {
    scope.checkpointed_boundary_authority = inactive;
    assert.deepEqual(render().boundary, baseline.boundary);
  }
  delete scope.checkpointed_boundary_authority;
  assert.deepEqual(render(), baseline);
});

test("required commands remain intact independently of additive hook prompt budgets", () => {
  const source = payload();
  const baseline = buildTurnEnvelope({ payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: "" });
  const command = "loopx inspect --registry /" + "route/".repeat(80) + "registry.json";
  const read = { kind: "fixture_read", command, reason: "Read the pending observation",
    source: "turn_start_capability_hook" };
  source.required_reads = [read];
  const ordinary = buildTurnEnvelope({ payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: "" });
  assert.equal((ordinary.compaction as JsonObject).budget_bytes, 8_192);
  assert.equal((ordinary.required_reads as JsonObject[])[0].command, command);
  assert.equal((ordinary.compaction as JsonObject).hook_prompt_budget_bytes, undefined);
  source.required_reads = [{ ...read, prompt_budget_bytes: 1_536 }];
  const active = buildTurnEnvelope({ payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: "" });
  assert.equal((active.required_reads as JsonObject[])[0].command, command);
  assert.equal((active.compaction as JsonObject).budget_bytes, 8_192 + 1_536);
  assert.equal((active.compaction as JsonObject).hook_prompt_budget_bytes, 1_536);
  assert.equal((active.compaction as JsonObject).envelope_utf8_bytes, Buffer.byteLength(JSON.stringify(active)));
  for (const field of ["action", "user", "scheduler", "execution_policy", "writeback"]) {
    assert.deepEqual(active[field], baseline[field]);
  }
  source.required_reads = [{ ...read, source: "other", prompt_budget_bytes: 1_536 }];
  const unrelated = buildTurnEnvelope({ payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: "" });
  assert.equal((unrelated.compaction as JsonObject).budget_bytes, 8_192);
  delete source.required_reads;
  assert.deepEqual(buildTurnEnvelope({ payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: "" }), baseline);
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
  const envelope = buildTurnEnvelope({payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
  assert.equal(envelope.replan_action_packet, null);
  assert.deepEqual((envelope.writeback as JsonObject).next_cli_actions, [command]);
  const signed = JSON.parse(JSON.stringify(turnEnvelopeActionSignatureDocument(envelope)));
  assert.deepEqual((signed.action as JsonObject).capability_intent, source.pending_capability_intent);
  (source.pending_capability_intent as JsonObject).command = "untrusted replacement";
  assert.throws(() => buildTurnEnvelope({payload: source, protocol_action_fields: protocolActionFields,
    scheduler_execution_args: ""}), /action is unsupported/);
});

test("Turn envelope transaction owns compaction and signature construction", () => {
  const source = payload();
  const envelope = buildTurnEnvelope({
    payload: source,
    protocol_action_fields: protocolActionFields,
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
  assert.deepEqual(capsule.protocol_action_packet, {
    schema_version: "protocol_action_packet_v0",
    present: true,
    summary_hash: "sha256:b5b6cd58e32de45a909adc2e02d56d9bf358039fa442871b1a25c6d27ae4101c",
    derivation_status: "verified",
    reconstruction_verified: true,
    llm_policy: "no_api",
    candidate_derivation_inputs: [
      "action",
      "user",
      "work_lane_contract",
      "automation_liveness",
      "scheduler",
    ],
  });
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
    protocol_action_fields: protocolActionFields,
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
    const envelope = buildTurnEnvelope({payload: source, protocol_action_fields: protocolActionFields,
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
    assert.deepEqual(quotaActionSignatureDocument(source, protocolActionFields),
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
  source.protocol_action_packet = {};
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
    protocol_action_fields: {
      actor: "agent",
      user_action_required: false,
      agent_action_required: false,
      quiet_noop_allowed: true,
      lane: "continuous_monitor",
      llm: "no_api",
      agent_action:
        "quiet until a material monitor transition, regression, or concrete blocker appears",
    },
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

  const signature = quotaActionSignatureDocument(source, protocolActionFields);
  assert.equal(signature.coverage, ACTION_SIGNATURE_COVERAGE_V3);
  const horizon = (signature.action as Record<string, unknown>)
    .planning_horizon as Record<string, unknown>;
  assert.equal(horizon.detail_refs, undefined);
  assert.equal(horizon.detail_refs_ref, "$.detail_ref");
});

test("v0 compaction metric preserves Unicode code-point compatibility", () => {
  const source = payload();
  source.reason = "推进一段 🚀";
  const envelope = buildTurnEnvelope({
    payload: source,
    protocol_action_fields: protocolActionFields,
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
    const envelope = buildTurnEnvelope({ payload: source, protocol_action_fields: {}, scheduler_execution_args: "" });
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
    protocol_action_fields: {},
    scheduler_execution_args: "",
  });
  assert.equal(
    (envelope.action_signature as Record<string, unknown>).source_decision_hash,
    "sha256:c68a197d600a6b89b6d2816a77e2de4c03e0cc6a7ac5821d039d9320ab341795",
  );
});

test("selected Todo text references preserve Unicode casefold compatibility", () => {
  const source = payload();
  source.recommended_action = "STRASSE";
  (source.selected_todo as Record<string, unknown>).text = "Straße";
  const interaction = source.interaction_contract as Record<string, unknown>;
  (interaction.agent_channel as Record<string, unknown>).primary_action = "STRASSE";
  const envelope = buildTurnEnvelope({
    payload: source,
    protocol_action_fields: protocolActionFields,
    scheduler_execution_args: "",
  });
  const selectedTodo = (envelope.action as Record<string, unknown>)
    .selected_todo as Record<string, unknown>;
  assert.equal(selectedTodo.text_ref, "action.recommended_action");
  assert.equal(selectedTodo.text, undefined);
});

test("signature document fails closed to the canonical signed dimensions", () => {
  const envelope = buildTurnEnvelope({
    payload: payload(),
    protocol_action_fields: protocolActionFields,
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
      protocol_action_fields: [],
      scheduler_execution_args: "",
    }),
    EffectRuntimeRequestError,
  );
  assert.throws(
    () => buildTurnEnvelope({
      payload: payload(),
      protocol_action_fields: protocolActionFields,
      scheduler_execution_args: [],
    }),
    EffectRuntimeRequestError,
  );
  const invalid = payload();
  invalid.open_count = "not-an-integer";
  assert.throws(
    () => buildTurnEnvelope({
      payload: invalid,
      protocol_action_fields: protocolActionFields,
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
        protocol_action_fields: protocolActionFields,
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
      protocol_action_fields: protocolActionFields,
      scheduler_execution_args: "",
    }),
    EffectRuntimeRequestError,
  );
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
    protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
  const result = render();
  assert.deepEqual((result.required_reads as JsonObject[]).map(x => x.command), reads.map(x => x.command));
  assert.equal((result.compaction as JsonObject).budget_bytes, 8_192 + 1_536);
  assert.equal((result.compaction as JsonObject).within_budget, false);
  assert.equal((result.action_signature as JsonObject).matches, true);
  const tampered = structuredClone(result);
  (tampered.required_reads as JsonObject[]).pop();
  assert.notDeepEqual(turnEnvelopeActionSignatureDocument(tampered), quotaActionSignatureDocument(source, protocolActionFields));
  reads[5].command += " --fresh";
  assert.notEqual((render().action_signature as JsonObject).source_hash, (result.action_signature as JsonObject).source_hash);
});


test("unavailable hook context is signed without suppressing independent work", () => {
  const source = payload();
  const render = () => buildTurnEnvelope({payload: source,
    protocol_action_fields: protocolActionFields, scheduler_execution_args: ""});
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
    assert.notDeepEqual(turnEnvelopeActionSignatureDocument(result), quotaActionSignatureDocument(source, protocolActionFields));
  }
});


test("captured details reuse one observation without changing action or default projection", () => {
  const source = payload();
  source.heartbeat_receipt = { turn_instance_id: "turn-observed" };
  const request = { payload: source, protocol_action_fields: protocolActionFields, scheduler_execution_args: "" };
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
