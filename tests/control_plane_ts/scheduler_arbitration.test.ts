import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {
  projectInteractionRequiredReads,
  projectSchedulerArbitration,
} from "../../loopx/control_plane/work_items/interaction_contract.ts";

function facts(mode = "bounded_delivery", user = false, attempt = true,
  delivery = true, quiet = false): JsonObject {
  return {schema_version: "loopx_interaction_contract_v0", mode,
    user_channel: {present: true, action_required: user},
    agent_channel: {present: true, must_attempt: attempt, delivery_allowed: delivery, quiet_noop_allowed: quiet},
    cli_channel: {}, agent_scope_frontier_actions: ["agent_scope_wait", "successor_replan_required",
      "terminal_no_followup", "peer_coordination_blocked", "agent_monitor_only", "monitor_quiet_skip", "heartbeat_settled_skip"]};
}

for (const [mode, user, attempt, delivery, quiet, disposition, reason] of [
  ["terminal_no_followup", false, false, false, true, "terminal_stop", "terminal_no_followup"],
  ["peer_coordination_blocked", false, false, false, true, "peer_coordination_wait", "peer_coordination_blocked"],
  ["agent_monitor_only", false, false, false, true, "agent_monitor_only_wait", "agent_monitor_only"],
  ["user_gate", true, false, false, false, "human_gate", "interaction_blocking_user_gate"],
  ["monitor_quiet_skip", true, false, false, false, "human_gate", "interaction_blocking_user_gate"],
  ["monitor_quiet_skip", false, true, true, false, "monitor_wait", "interaction_monitor_quiet_wait"],
  ["heartbeat_settled_skip", false, false, false, false, "settled_replay", "interaction_heartbeat_settled_replay"],
  ["successor_replan_required", false, true, false, false, "active_work", "interaction_successor_replan_required"],
  ["successor_replan_required", false, false, false, true, "agent_scope_wait", "interaction_agent_scope_wait"],
  ["agent_scope_wait", false, true, false, false, "agent_scope_wait", "interaction_agent_scope_wait"],
  ["mapped_noop_if_unchanged", false, true, true, false, "unchanged_wait", "interaction_unchanged_wait"],
  ["automation_prompt_upgrade", false, true, false, false, "active_work", "interaction_agent_attempt_required"],
  ["bounded_delivery_with_user_notice", true, true, true, false, "active_work", "interaction_agent_attempt_required"],
  ["quiet_skip", false, false, false, true, "quiet_wait", "interaction_quiet_noop_allowed"],
  ["unknown_nonempty_mode", false, false, false, false, "quiet_wait", "interaction_delivery_not_allowed"],
] as const) {
  test(`${mode}: channel ${user}/${attempt}/${delivery}/${quiet} retains ${disposition} precedence`, () => {
    const input = facts(mode, user, attempt, delivery, quiet);
    const before = structuredClone(input);
    assert.deepEqual(projectSchedulerArbitration(input), {mode, disposition, reason_code: reason, errors: []});
    assert.deepEqual(input, before);
  });
}

test("a settlement retry notice is non-gating only with both exact source facts", () => {
  const source = facts("monitor_quiet_skip", true, false, false, false);
  source.user_channel = {...source.user_channel as JsonObject, non_gating_notice: true};
  source.agent_channel = {...source.agent_channel as JsonObject, wait_source: "turn_settlement"};
  assert.deepEqual(projectSchedulerArbitration(source), {mode: "monitor_quiet_skip", errors: [],
    disposition: "quiet_wait", reason_code: "blocked_wait"});
  for (const patch of [{non_gating_notice: false}, {non_gating_notice: null}]) {
    assert.equal(projectSchedulerArbitration({...source,
      user_channel: {...source.user_channel as JsonObject, ...patch}}).disposition, "human_gate");
  }
  assert.equal(projectSchedulerArbitration({...source,
    agent_channel: {...source.agent_channel as JsonObject, wait_source: "another_source"}}).disposition, "human_gate");
});

test("structural diagnostics precede semantic diagnostics in the legacy stable order", () => {
  const invalid = {schema_version: "future", mode: "", user_channel: {present: false},
    agent_channel: {present: false}, cli_channel: {spend_allowed_now: 1, spend_after_validation: "false"},
    agent_scope_frontier_actions: []};
  assert.deepEqual(projectSchedulerArbitration(invalid), {mode: "", disposition: "consistency_repair",
    reason_code: "scheduler_interaction_contract_inconsistent", errors: [
      "interaction_contract.schema_version_mismatch", "interaction_contract.mode_missing",
      "interaction_contract.user_channel_missing", "interaction_contract.agent_channel_missing",
      "interaction_contract.user_channel.action_required_must_be_boolean",
      "interaction_contract.agent_channel.must_attempt_must_be_boolean",
      "interaction_contract.agent_channel.delivery_allowed_must_be_boolean",
      "interaction_contract.agent_channel.quiet_noop_allowed_must_be_boolean",
      "interaction_contract.cli_channel.spend_allowed_now_must_be_boolean",
      "interaction_contract.cli_channel.spend_after_validation_must_be_boolean",
    ]});
  const semantic = facts("heartbeat_settled_skip", true, false, true, true);
  semantic.cli_channel = {spend_after_validation: true};
  assert.deepEqual(projectSchedulerArbitration(semantic).errors, [
    "interaction_contract.delivery_without_attempt",
    "interaction_contract.quiet_noop_conflicts_with_required_action",
    "interaction_contract.settled_conflicts_with_open_action",
    "interaction_contract.settled_conflicts_with_spend",
  ]);
});

for (const [channel, field] of [
  ["user_channel", "action_required"], ["agent_channel", "must_attempt"],
  ["agent_channel", "delivery_allowed"], ["agent_channel", "quiet_noop_allowed"],
  ["cli_channel", "spend_allowed_now"], ["cli_channel", "spend_after_validation"],
] as const) {
  test(`${channel}.${field} cannot coerce a non-boolean into authority`, () => {
    for (const invalid of [null, 0, 1, "false", []]) {
      const source = facts();
      source[channel] = {...source[channel] as JsonObject, [field]: invalid};
      const projected = projectSchedulerArbitration(source);
      assert.equal(projected.disposition, "consistency_repair");
      assert.ok((projected.errors as string[]).includes(`interaction_contract.${channel}.${field}_must_be_boolean`));
    }
  });
}

for (const [mode, diagnostic] of [
  ["heartbeat_settled_skip", "settled_conflicts_with_spend"],
  ["terminal_no_followup", "terminal_conflicts_with_open_action"],
  ["peer_coordination_blocked", "terminal_conflicts_with_open_action"],
  ["agent_monitor_only", "terminal_conflicts_with_open_action"],
] as const) {
  test(`${mode} refuses each spend flag`, () => {
    for (const field of ["spend_allowed_now", "spend_after_validation"]) {
      const source = facts(mode, false, false, false, mode !== "heartbeat_settled_skip");
      source.cli_channel = {[field]: true};
      const projected = projectSchedulerArbitration(source);
      assert.equal(projected.disposition, "consistency_repair");
      assert.ok((projected.errors as string[]).includes(`interaction_contract.${diagnostic}`));
    }
  });
}

test("settled replay refuses every open-execution channel independently", () => {
  for (const [channel, field] of [["user_channel", "action_required"],
    ["agent_channel", "must_attempt"], ["agent_channel", "delivery_allowed"]]) {
    const source = facts("heartbeat_settled_skip", false, false, false, false);
    source[channel] = {...source[channel] as JsonObject, [field]: true};
    const projected = projectSchedulerArbitration(source);
    assert.equal(projected.disposition, "consistency_repair");
    assert.ok((projected.errors as string[]).includes("interaction_contract.settled_conflicts_with_open_action"));
  }
});

test("scope membership is explicit and cannot override a higher-priority disposition", () => {
  const source = facts("mapped_noop_if_unchanged", false, false, false, true);
  source.agent_scope_frontier_actions = ["mapped_noop_if_unchanged"];
  assert.equal(projectSchedulerArbitration(source).disposition, "agent_scope_wait");
  source.mode = "successor_replan_required";
  assert.equal(projectSchedulerArbitration(source).disposition, "quiet_wait");
  const capability = facts("governed_capability_intent", true, true, true, false);
  assert.equal(projectSchedulerArbitration(capability).disposition, "active_work");
});

test("the required-read reply retains its reads and complete scheduler disposition", () => {
  const arbitration = facts();
  const result = projectInteractionRequiredReads({required_reads: [], should_run: false,
    delivery_allowed: false, selection_required: false, has_replan: false, settlement_only: false,
    goal_acceptance_enabled: false, goal_id: null, arbitration_facts: arbitration});
  assert.deepEqual(result, {required_reads: [], arbitration: {mode: "bounded_delivery",
    disposition: "active_work", reason_code: "interaction_agent_attempt_required", errors: []}});
});

function datedWait(): JsonObject {
  const resume = "resume_at:2099-01-01T01:00:00Z";
  return {...facts("agent_scope_wait", false, false, false, true),
    effective_action: "agent_scope_wait", agent_id: "scheduled-agent",
    heartbeat_receipt: {status: "committed"},
    vision_wait_state: {schema_version: "goal_vision_wait_state_v0", state: "waiting",
      reason_code: "exact_blocked_successor", agent_id: "scheduled-agent",
      automatic_resume: true, waiting_todo_count: 1, selected_todo_id: "todo_scheduled",
      selected_todo_status: "deferred", selected_todo_claimed_by: "scheduled-agent",
      resume_when: resume, resume_condition: {schema_version: "todo_resume_condition_v0",
        kind: "resume_at", resume_when: resume, target: "2099-01-01T01:00:00Z",
        satisfied: false, scheduled_for: "2099-01-01T01:00:00Z", evaluated_at: "2099-01-01T00:30:00Z",
        clock_provider: "runtime_clock", material_change: false, material_change_generation: 0,
        resume_receipt: null}}};
}

test("only an unbound pending automatic dated successor carries a scheduled recheck", () => {
  const input = datedWait();
  const before = structuredClone(input);
  assert.deepEqual(projectSchedulerArbitration(input), {mode: "agent_scope_wait", errors: [],
    disposition: "agent_scope_wait", reason_code: "interaction_agent_scope_wait",
    scheduled_recheck_after_seconds: 1800});
  assert.deepEqual(input, before);
  for (const claimed of [null, ""]) {
    assert.equal(projectSchedulerArbitration({...input, vision_wait_state: {
      ...input.vision_wait_state as JsonObject, selected_todo_claimed_by: claimed,
    }}).scheduled_recheck_after_seconds, 1800);
  }
});

test("unproven waits cannot relax finite scheduler supervision", () => {
  const input = datedWait();
  const wait = input.vision_wait_state as JsonObject;
  const condition = wait.resume_condition as JsonObject;
  const patches: JsonObject[] = [
    {effective_action: "agent_scope_exhausted"}, {agent_id: "other-agent"},
    {heartbeat_receipt: {status: "pending"}},
    {heartbeat_receipt: {status: "committed", settlement_identity: {todo_id: "todo_scheduled"}}},
    {heartbeat_receipt: {status: "replayed", pending_action_selection: {}}},
    ...[{automatic_resume: false}, {schema_version: "future"}, {state: "ready"},
      {waiting_todo_count: 2}, {selected_todo_status: "done"}, {selected_todo_claimed_by: "other-agent"},
      {agent_id: "other-agent"}, {resume_when: "resume_at:2098-01-01T00:00:00Z"}]
      .map(patch => ({vision_wait_state: {...wait, ...patch}})),
    ...[{satisfied: true}, {evaluated_at: condition.scheduled_for}, {evaluated_at: "invalid"},
      {clock_provider: "caller_clock"}, {material_change: true}, {resume_receipt: {}},
      {scheduled_for: "2098-01-01T00:00:00Z"}, {kind: "todo_done"}]
      .map(patch => ({vision_wait_state: {...wait, resume_condition: {...condition, ...patch}}})),
  ];
  for (const patch of patches) {
    assert.equal(projectSchedulerArbitration({...input, ...patch}).scheduled_recheck_after_seconds,
      undefined, JSON.stringify(patch));
  }
  for (const higher of [facts("user_gate", true, false, false, false),
    facts("terminal_no_followup", false, false, false, true), facts()]) {
    const result = projectSchedulerArbitration({...input, ...higher});
    assert.equal(result.scheduled_recheck_after_seconds, undefined);
    assert.notEqual(result.disposition, "agent_scope_wait");
  }
  for (const field of ["spend_allowed_now", "spend_after_validation"]) {
    assert.equal(projectSchedulerArbitration({...input, cli_channel: {[field]: true}})
      .scheduled_recheck_after_seconds, undefined);
  }
});

test("only a proven dependency wait keeps source polling alive without a Turn obligation", () => {
  for (const kind of ["todo_done", "monitor_changed"]) {
    const input = datedWait();
    const wait = input.vision_wait_state as JsonObject;
    const resume = `${kind}:todo_dependency`;
    const condition = {schema_version: "todo_resume_condition_v0", kind, resume_when: resume,
      target: "todo_dependency", target_todo_id: "todo_dependency", satisfied: false,
      target_status: "open", target_archive_state: null,
      target_task_class: kind === "todo_done" ? "advancement_task" : "continuous_monitor",
      baseline_generation: 0, material_change_generation: 0,
      target_next_due_at: "2099-01-01T00:00:00Z", target_expires_at: null,
      evaluated_at: "2026-09-01T00:00:00Z", clock_provider: "runtime_clock"};
    input.vision_wait_state = {...wait, resume_when: resume, resume_monitor_generation: 0, resume_condition: condition};
    assert.deepEqual(projectSchedulerArbitration(input), {mode: "agent_scope_wait", errors: [],
      disposition: "agent_scope_wait", reason_code: "interaction_agent_scope_wait", pending_dependency_wait: true});
    const monitor = {...input, ...facts("monitor_quiet_skip", false, false, false, true),
      effective_action: "monitor_quiet_skip"};
    assert.deepEqual(projectSchedulerArbitration(monitor), {mode: "monitor_quiet_skip", errors: [],
      disposition: "monitor_wait", reason_code: "interaction_monitor_quiet_wait", pending_dependency_wait: true});
    for (const patch of [{vision_wait_state: undefined}, {heartbeat_receipt: {status: "pending"}},
      {heartbeat_receipt: {status: "replayed", settlement_identity: {todo_id: "todo_scheduled"}}},
      {cli_channel: {spend_allowed_now: true}}, facts("normal_run"),
      facts("user_gate", true, false, false, false)]) {
      assert.equal(projectSchedulerArbitration({...monitor, ...patch}).pending_dependency_wait, undefined);
    }
    const pending = input.vision_wait_state as JsonObject;
    for (const patch of [
      {vision_wait_state: {...pending, resume_condition: {...condition, target_status: undefined}}},
      {vision_wait_state: {...pending, selected_todo_claimed_by: "another-agent"}},
      {vision_wait_state: {...pending, waiting_todo_count: 2}},
      {heartbeat_receipt: {status: "committed", settlement_identity: {todo_id: wait.selected_todo_id}}},
      {heartbeat_receipt: {status: "replayed", pending_action_selection: {}}},
      {effective_action: "normal_run"},
      {cli_channel: {spend_allowed_now: true}},
      {cli_channel: {spend_after_validation: true}},
      facts("user_gate", true, false, false, false),
      facts("monitor_quiet_skip", false, false, false, true),
      facts("terminal_no_followup", false, false, false, true),
      facts("successor_replan_required", false, true, false, false),
      facts(),
    ]) {
      const result = projectSchedulerArbitration({...input, ...patch});
      assert.equal(result.pending_dependency_wait, undefined, JSON.stringify(patch));
      assert.equal(result.scheduled_recheck_after_seconds, undefined);
    }
  }
});
