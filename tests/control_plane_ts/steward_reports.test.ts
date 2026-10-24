import assert from "node:assert/strict";
import test from "node:test";
import {planStewardReport} from "../../loopx/control_plane/collaboration/steward_reports.ts";
import {admitStewardManagedTurn, bindStewardManagedActor} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";

function original() {
  const binding = {binding_id: "b".repeat(24), transport_ref: "private-steward", provider_ref: "c".repeat(24),
    operator_ref: "d".repeat(24), project_ref: "a".repeat(24), context_kind: "steward",
    grant: "portfolio_read", enabled: true, executor_endpoint_id: "codex", audience: "owner"};
  const source = "e".repeat(24);
  const session = {session_id: "f".repeat(32), agent_id: "codex", goal_id: "loopx-manager", status: "ready",
    channel_id: `manager.external.native.${binding.binding_id}.${source}`,
    steward_context: {...binding, source_ref: source}};
  const workspace = "/runtime/chat/manager-workspaces/original";
  const goal = {goal_id: `steward-agent-${binding.binding_id}`, goal_instance_id: null,
    creation_operation_id: `steward-managed:${binding.binding_id}:${source}`,
    workspace_path: workspace, registered_agents: ["steward"]};
  const params = {binding, session, goal, workspace_path: workspace, queued_turn_count: 0,
    observation: {verified: true, transport_ref: binding.transport_ref, provider_ref: binding.provider_ref,
      operator_ref: binding.operator_ref}};
  const actor = bindStewardManagedActor(params);
  return {...params, binding: {...binding, managed_actor: actor}, actor, goal_active: true, source};
}

test("report consent fixes the original actor, source and private audience without starting work", () => {
  const p = original();
  const proposed = {enabled: true, profile_preset: "portfolio-progress",
    route_ref: `steward-private:${p.binding.binding_id}:${p.source}`};
  const route = {binding_id: p.binding.binding_id, source_ref: p.source, session_id: p.session.session_id,
    provider_ref: p.binding.provider_ref, operator_ref: p.binding.operator_ref};
  const input = {...p, configuration: null, proposed, route, operation: "enable"};
  const before = structuredClone(input);
  const first = planStewardReport(input);
  assert.deepEqual(input, before);
  assert.deepEqual(first.actor, p.actor);
  assert.equal(first.model_invoked, false);
  const retained = {...input, proposed_subscription_revision: "new", pending_report:
    {prepared: true, subscription_revision: "original", verified_publication: false}};
  assert.equal(planStewardReport(retained).gate, "report_window_pending");
  assert.equal(planStewardReport({...retained, proposed_subscription_revision: "original"}).gate, null);
  assert.equal(planStewardReport({...retained, pending_report: {...retained.pending_report,
    verified_publication: true}}).gate, null);
  assert.equal(planStewardReport({...retained, pending_report: {...retained.pending_report,
    retired: true}}).gate, null);
  const cancel = {...retained, operation: "cancel", proposed: {...proposed, enabled: false}};
  assert.equal(planStewardReport(cancel).retires_failed_window, false);
  assert.equal(planStewardReport({...cancel, pending_report: {...retained.pending_report,
    failure_ref: "known-terminal-failure"}}).retires_failed_window, true);
  assert.equal(planStewardReport({...cancel, pending_report: {...retained.pending_report,
    failure_ref: "known-terminal-failure", retired: true}}).retires_failed_window, false);
  assert.notEqual(first.plan_id, planStewardReport({...input, proposed: {...proposed, timezone: "UTC"}}).plan_id);
  for (const invalid of [
    {...input, binding: {...p.binding, audience: "group"}},
    {...input, route: {...route, session_id: "0".repeat(32)}},
    {...input, route: {...route, operator_ref: "0".repeat(24)}},
    {...input, configuration: {...proposed, route_ref: "another-channel"}},
    {...input, proposed: {...proposed, route_ref: "another-channel"}},
    {...input, goal_active: false},
  ]) assert.throws(() => planStewardReport(invalid));
});

test("a model report requires the exact live calendar window and original task, never a watch", () => {
  const p = original();
  const request = {operation: "native_turn", host: "codex-app-server", actor: p.actor,
    turn_instance_id: "report.cadence_original", todo_id: "todo_report", validation_command: ["python", "-c", "validate_generation()"],
    work_kind: "report", report_window_id: "cadence_original", report_subscription_revision: "sha256:original"};
  const window = {window_id: request.report_window_id, subscription_revision: request.report_subscription_revision,
    goal_id: p.actor.goal_id, agent_id: p.actor.agent_id};
  const subscription = {enabled: true, profile_preset: "portfolio-progress",
    route_ref: `steward-private:${p.binding.binding_id}:${p.source}`, effective_revision: window.subscription_revision};
  const todo = {todo_id: request.todo_id, target_key: window.window_id,
    action_kind: "generate_periodic_report", claimed_by: "steward", status: "open"};
  const input = {...p, request, report_window: window, report_subscription: subscription, report_todo: todo};
  assert.equal(admitStewardManagedTurn(input).observation_only, false);
  const recovery = {...input, report_todo: {...todo, status: "done", completion_turn_key: request.turn_instance_id},
    report_settlement_recovery: {schema_version: "loopx_turn_journal_inspection_v1", ok: true,
      goal_matches: true, owner_matches: true, turn_key_matches: true,
      journal_consistent: true, journal_status: "failed", recovery_decision:
      {action: "continue", reinvoke_host: false, resume_from: "quota_spend"}}};
  assert.equal(admitStewardManagedTurn(recovery).observation_only, false);
  const validation = {...input, report_settlement_recovery: {...recovery.report_settlement_recovery,
    completed_phases: ["host_execute", "typed_result"], recovery_decision:
    {action: "continue", reinvoke_host: false, resume_from: "validation"}}};
  assert.equal(admitStewardManagedTurn(validation).observation_only, false);
  for (const invalid of [
    {...validation, preparing_model_context: true},
    {...validation, report_todo: {...todo, todo_id: "another-task"}},
    {...validation, report_subscription: {...subscription, enabled: false}},
    {...validation, report_settlement_recovery: {...validation.report_settlement_recovery, journal_status: "in_progress"}},
    ...[[], ["host_execute"], ["typed_result", "host_execute"]].map(completed_phases =>
      ({...validation, report_settlement_recovery: {...validation.report_settlement_recovery, completed_phases}})),
    {...validation, report_settlement_recovery: {...validation.report_settlement_recovery, recovery_decision:
      {action: "continue", reinvoke_host: true, resume_from: "validation"}}},
  ]) assert.throws(() => admitStewardManagedTurn(invalid));
  for (const invalid of [
    {...recovery, report_settlement_recovery: {...recovery.report_settlement_recovery, journal_consistent: false}},
    {...recovery, report_todo: {...recovery.report_todo, completion_turn_key: "another-original-turn"}},
    {...recovery, report_todo: {...todo, status: "open"}},
    {...recovery, preparing_model_context: true},
    ...["host_execute", "typed_result", "validation"].map(resume_from => ({...recovery,
      report_settlement_recovery: {...recovery.report_settlement_recovery, recovery_decision:
        {action: "continue", reinvoke_host: false, resume_from}}})),
    {...recovery, report_settlement_recovery: {...recovery.report_settlement_recovery, recovery_decision:
      {action: "continue", reinvoke_host: true, resume_from: "quota_spend"}}},
  ]) assert.throws(() => admitStewardManagedTurn(invalid));
  const changed = {...input, binding: {...p.binding, goal_scope: "all_registered"},
    session: {...p.session, active_turn_id: "running", manager_authorization_scope_id: "old_scope",
      steward_context: {...p.session.steward_context, goal_scope: "all_registered"}},
    scope: {current: "new_scope", without_actor: "other_scope"},
    turn: {session_id: p.session.session_id, turn_id: "running", status: "running", origin: "native_loopx",
      loopx_execution: true, loopx_request: request}};
  assert.throws(() => admitStewardManagedTurn(changed));
  const preparation = admitStewardManagedTurn({...changed, preparing_model_context: true});
  assert.equal(preparation.context_rebind_required, true);
  assert.equal(preparation.refresh_original_scope, false); // Only the existing context owner may change it.
  assert.throws(() => admitStewardManagedTurn({...changed, preparing_model_context: true,
    binding: {...changed.binding, goal_scope: "selected"}}));
  assert.throws(() => admitStewardManagedTurn({...changed, preparing_model_context: "true"}));
  for (const invalid of [
    {...input, request: {...request, validation_command: null}},
    {...input, report_subscription: {...subscription, enabled: false}},
    {...input, report_subscription: {...subscription, effective_revision: "changed"}},
    {...input, report_window: {...window, window_id: "another"}},
    {...input, report_todo: {...todo, claimed_by: "other"}},
    {...input, report_todo: {...todo, action_kind: "local_runtime_observe"}},
    {...input, report_todo: {...todo, status: "done"}},
  ]) assert.throws(() => admitStewardManagedTurn(invalid));
});

test("a retained pre-publication launch refusal can resume only its original native transaction", () => {
  const p = original();
  const prior = {operation: "native_turn", host: "codex-app-server", actor: p.actor,
    turn_instance_id: "report.cadence_original", todo_id: "todo_report", validation_command: ["python", "validate.py"],
    work_kind: "report", report_window_id: "cadence_original", report_subscription_revision: "sha256:original"};
  const key = "sha256:" + "a".repeat(64);
  const request = {...prior, resume_turn_key: key};
  const result = {schema_version: "loopx_turn_execution_v0", mode: "run_once", ok: false,
    error_code: "stale_goal_authority", error: "first-party Host runtime rejected: stale_goal_authority",
    effects_scope: "current_invocation", effects: {host_invoked: null, state_written: null, quota_spent: null},
    resume_turn_key: key};
  const source = {session_id: p.session.session_id, status: "completed", origin: "native_loopx", loopx_execution: true,
    delta_count: 0, upstream_turn_id: null, loopx_request: prior, response: {native_loopx: result}};
  const inspection = {schema_version: "loopx_turn_journal_inspection_v1", ok: true,
    goal_matches: true, owner_matches: true, turn_key_matches: true, journal_consistent: true,
    journal_status: "in_progress", completed_phases: [],
    recorded_effects: {host_invoked: null, state_written: false, quota_spent: false, scheduler_acknowledged: false},
    recovery_decision: {action: "continue", reinvoke_host: true, resume_from: "host_execute"}};
  const proof = {original: source, inspection, turn_key: key, host_attempt_count: 1, generation_present: false};
  const input = {...p, request, report_launch_recovery: proof,
    report_window: {window_id: prior.report_window_id, subscription_revision: prior.report_subscription_revision,
      goal_id: p.actor.goal_id, agent_id: p.actor.agent_id},
    report_subscription: {enabled: true, profile_preset: "portfolio-progress",
      route_ref: `steward-private:${p.binding.binding_id}:${p.source}`, effective_revision: prior.report_subscription_revision},
    report_todo: {todo_id: prior.todo_id, target_key: prior.report_window_id,
      claimed_by: "steward", action_kind: "generate_periodic_report", status: "open"}};
  assert.equal(admitStewardManagedTurn(input).observation_only, false);
  for (const invalid of [
    {...input, report_launch_recovery: null},
    {...input, request: prior},
    {...input, request: {...request, resume_turn_key: "sha256:" + "b".repeat(64)}},
    {...input, report_launch_recovery: {...proof, turn_key: "sha256:" + "b".repeat(64)}},
    {...input, report_launch_recovery: {...proof, host_attempt_count: 2}},
    {...input, report_launch_recovery: {...proof, generation_present: true}},
    ...["failed", "running", "interrupted"].map(status => ({...input,
      report_launch_recovery: {...proof, original: {...source, status}}})),
    {...input, report_launch_recovery: {...proof, original: {...source, delta_count: 1}}},
    {...input, report_launch_recovery: {...proof, original: {...source, upstream_turn_id: "actual-start"}}},
    {...input, report_launch_recovery: {...proof, original: {...source,
      loopx_request: {...prior, todo_id: "todo_other"}}}},
    ...["transport_lost", "goal_launch_not_allowed", "unknown"].map(error_code => ({...input,
      report_launch_recovery: {...proof, original: {...source, response: {native_loopx: {...result, error_code}}}}})),
    {...input, report_launch_recovery: {...proof, original: {...source, response: {native_loopx:
      {...result, effects: {state_written: true}}}}}},
    ...["goal_matches", "owner_matches", "turn_key_matches", "journal_consistent"].map(key => ({...input,
      report_launch_recovery: {...proof, inspection: {...inspection, [key]: false}}})),
    {...input, report_launch_recovery: {...proof, inspection: {...inspection, completed_phases: ["host_execute"]}}},
    {...input, report_launch_recovery: {...proof, inspection: {...inspection,
      recorded_effects: {...inspection.recorded_effects, quota_spent: null}}}},
    {...input, report_launch_recovery: {...proof, inspection: {...inspection,
      recorded_effects: {...inspection.recorded_effects, host_invoked: true}}}},
    {...input, report_subscription: {...input.report_subscription, enabled: false}},
    {...input, goal_active: false},
    {...input, queued_turn_count: 1},
  ]) assert.throws(() => admitStewardManagedTurn(invalid));
});
