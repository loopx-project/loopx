import assert from "node:assert/strict";
import test from "node:test";
import {planStewardReport} from "../../loopx/control_plane/collaboration/steward_reports.ts";
import {admitManagedSessionIdentity, admitManagedSessionTurn, admitStewardManagedTurn, bindStewardManagedActor, observeManagedWork} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";
import {resolveConversationScope} from "../../loopx/control_plane/collaboration/conversation_scope.ts";

test("a managed work identity is registration-bound and never chosen from the executor or legacy history", () => {
  const goal = {goal_id: "project", goal_instance_id: "original", registered_agents: ["writer", "reviewer"], workspace_path: "/workspace"};
  const input = {goal, goal_active: true, workspace_path: "/workspace", managed_agent_id: "writer"};
  const identity = admitManagedSessionIdentity(input);
  assert.deepEqual(identity, {managed_agent_id: "writer", channel_id: "agent.writer", goal_instance_id: "original"});
  assert.notDeepEqual(admitManagedSessionIdentity({...input, managed_agent_id: "reviewer"}), identity);
  for (const changed of [{managed_agent_id: "codex"}, {managed_agent_id: ["writer"]}, {goal_active: false},
    {workspace_path: "/other"}, {goal: {...goal, goal_instance_id: {unverified: true}}}]) {
    assert.throws(() => admitManagedSessionIdentity({...input, ...changed}));
  }
  const session = {...identity, goal_id: goal.goal_id, session_id: "a".repeat(32), session_mode: "managed_runtime",
    agent_id: "codex", executor_endpoint_id: "codex", adapter_kind: "codex_app_server", managed_workspace_path: "/workspace"};
  assert.equal(resolveConversationScope(session).kind, "owner_goal");
  assert.equal(resolveConversationScope({...session, channel_id: "goal.project"}).kind, "unavailable");
  assert.equal(resolveConversationScope({...session, origin: "lark"}).kind, "unavailable");
  const request = {operation: "native_turn", host: "codex-app-server", work_kind: "native_turn",
    turn_instance_id: "one-operation", todo_id: null, validation_command: null,
    actor: {schema_version: "loopx_managed_session_actor_v0", goal_id: goal.goal_id, goal_instance_id: "original",
      agent_id: "writer", session_id: session.session_id, workspace_path: "/workspace"}};
  const params = {goal, goal_active: true, session, request, queued_turn_count: 0};
  assert.deepEqual(admitManagedSessionTurn(params).actor, request.actor);
  for (const changed of [{managed_agent_id: "reviewer"}, {managed_agent_id: undefined, channel_id: "goal.project"},
    {goal_instance_id: "replaced"}, {managed_workspace_path: "/other"}, {executor_endpoint_id: "other"}]) {
    assert.throws(() => admitManagedSessionTurn({...params, session: {...session, ...changed}}));
  }
  const watch = {todo_id: "todo_watch", target_key: `managed-session-watch.${session.session_id}`,
    task_class: "continuous_monitor", action_kind: "local_runtime_observe", claimed_by: "writer",
    watch_only: true, status: "open", next_due_at: "2030-01-01T00:00:00Z"};
  const watched = {...params, request: {...request, work_kind: "monitor", todo_id: watch.todo_id,
    monitor_deadline: watch.next_due_at, monitor_lease_version: 0}, monitor_todo: watch};
  assert.equal(admitManagedSessionTurn(watched).observation_only, true);
  for (const changed of [{target_key: "steward-portfolio-watch"}, {claimed_by: "reviewer"},
    {target_key: "managed-session-watch.other-session"}, {watch_only: false}, {status: "blocked"},
    {action_kind: "business_work"}, {next_due_at: "2030-01-02T00:00:00Z"}]) {
    assert.throws(() => admitManagedSessionTurn({...watched, monitor_todo: {...watch, ...changed}}));
  }
  assert.throws(() => admitManagedSessionTurn({...params, request: {...request, monitor_deadline: watch.next_due_at}}));
});

test("local native work observation excludes watch bookkeeping and retains real work changes", () => {
  const goal = {goal_id: "project", goal_instance_id: "original"};
  const task = {todo_id: "todo_work", task_class: "advancement_task", status: "open", title: "Useful work"};
  const watch = {todo_id: "todo_watch", task_class: "continuous_monitor", next_due_at: "2030-01-01"};
  const source = {goal, actor: {goal_id: goal.goal_id, goal_instance_id: goal.goal_instance_id}, todos: [task, watch]};
  const first = observeManagedWork(source);
  const unchanged = observeManagedWork({...source, previous_hash: first.result_hash,
    todos: [{...watch, next_due_at: "2031-01-01", result_hash: "changed", material_change_generation: 99}, task]});
  assert.equal(unchanged.result_hash, first.result_hash);
  assert.equal(unchanged.material_change, false);
  assert.equal(first.observed_task_count, 1);
  assert.equal(observeManagedWork({...source, previous_hash: first.result_hash, todos: [{...task, status: "done"}]}).material_change, true);
  assert.throws(() => observeManagedWork({...source, goal: {...goal, goal_instance_id: "replacement"}}));
  assert.throws(() => observeManagedWork({...source, todos: undefined}));
});

test("a local calendar report retains its exact original Session, subscription, window and native Task", () => {
  const goal = {goal_id: "project", goal_instance_id: "original", registered_agents: ["writer", "peer"], workspace_path: "/workspace"};
  const session = {goal_id: goal.goal_id, goal_instance_id: goal.goal_instance_id, session_id: "a".repeat(32),
    channel_id: "agent.writer", session_mode: "managed_runtime", managed_agent_id: "writer", agent_id: "codex",
    executor_endpoint_id: "codex", adapter_kind: "codex_app_server", managed_workspace_path: goal.workspace_path};
  const actor = {schema_version: "loopx_managed_session_actor_v0", goal_id: goal.goal_id, goal_instance_id: goal.goal_instance_id,
    session_id: session.session_id, agent_id: "writer", workspace_path: goal.workspace_path};
  const request = {operation: "native_turn", host: "codex-app-server", work_kind: "report", actor,
    turn_instance_id: "managed-session.report.original", todo_id: "todo_report", validation_command: null,
    report_window_id: "original-window", report_subscription_revision: "original-subscription"};
  const subscription = {enabled: true, profile_preset: "weekly-progress", route_ref: `managed-session:${session.session_id}`,
    effective_revision: request.report_subscription_revision};
  const window = {goal_id: goal.goal_id, agent_id: "writer", window_id: request.report_window_id,
    subscription_revision: request.report_subscription_revision};
  const todo = {todo_id: request.todo_id, target_key: window.window_id, action_kind: "generate_periodic_report", claimed_by: "writer", status: "open"};
  const input = {session, goal, request, goal_active: true, queued_turn_count: 0,
    report_window: window, report_subscription: subscription, report_todo: todo};
  assert.equal(admitManagedSessionTurn(input).observation_only, false);
  for (const changed of [
    {...input, goal_active: false}, {...input, queued_turn_count: 1},
    {...input, session: {...session, managed_agent_id: "peer"}},
    {...input, session: {...session, session_mode: "attached_host"}},
    {...input, session: {...session, goal_instance_id: "replacement"}},
    {...input, report_window: {...window, goal_id: "another"}},
    {...input, report_window: {...window, agent_id: "peer"}},
    {...input, report_window: {...window, window_id: "next-window"}},
    {...input, report_subscription: {...subscription, enabled: false}},
    {...input, report_subscription: {...subscription, effective_revision: "changed"}},
    {...input, report_subscription: {...subscription, profile_preset: "portfolio-progress"}},
    {...input, report_subscription: {...subscription, route_ref: "managed-session:" + "b".repeat(32)}},
    {...input, report_todo: {...todo, target_key: "another-window"}},
    {...input, report_todo: {...todo, claimed_by: "peer"}},
    {...input, report_todo: {...todo, status: "done"}},
    {...input, request: {...request, validation_command: ["a-caller-command"]}},
    {...input, request: {...request, profile: "another-runtime"}},
    {...input, request: {...request, work_kind: "native_turn"}},
  ]) assert.throws(() => admitManagedSessionTurn(changed));
  const inspection = {schema_version: "loopx_turn_journal_inspection_v1", ok: true,
    goal_matches: true, owner_matches: true, turn_key_matches: true, journal_consistent: true,
    journal_status: "failed", completed_phases: ["host_execute", "typed_result"],
    recovery_decision: {action: "continue", resume_from: "validation", reinvoke_host: false}};
  assert.equal(admitManagedSessionTurn({...input, report_settlement_recovery: inspection}).observation_only, false);
  for (const changed of [{owner_matches: false}, {journal_consistent: false}, {completed_phases: ["host_execute"]},
    {recovery_decision: {...inspection.recovery_decision, reinvoke_host: true}},
    {recovery_decision: {...inspection.recovery_decision, resume_from: "host_execute"}}]) {
    assert.throws(() => admitManagedSessionTurn({...input, report_settlement_recovery: {...inspection, ...changed}}));
  }
});

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

test("only a proven original pre-execution replan refusal admits one correlated report continuation", () => {
  const p = original();
  const request = {operation: "native_turn", host: "codex-app-server", actor: p.actor,
    turn_instance_id: "report.cadence_original", todo_id: "todo_report", validation_command: ["python", "validate.py"],
    work_kind: "report", report_window_id: "cadence_original", report_subscription_revision: "sha256:original"};
  const refusal = {schema_version: "loopx_turn_selection_rejection_v0", source: "quota.should-run",
    requested_todo_id: request.todo_id, state: "deferred", reason_code: "autonomous_replan",
    recovery_action: "reenter_guard_without_selection", delivery_preemptions: ["autonomous_replan", "delivery_not_allowed"],
    status_health_ok: true, contract_error_count: 0};
  const effects = {host_invoked: false, state_written: false, quota_spent: false, scheduler_acknowledged: false};
  const result = {schema_version: "loopx_turn_execution_v0", mode: "run_once", ok: false,
    error_code: "turn_todo_selection_deferred", effects_scope: "current_invocation", effects, selection_rejection: refusal};
  const source = {session_id: p.session.session_id, status: "completed", origin: "native_loopx", loopx_execution: true,
    delta_count: 0, upstream_turn_id: null, loopx_request: request, response: {native_loopx: result}};
  const proof = {original: source, generation_present: false};
  const input = {...p, request, report_selection_recovery: proof,
    report_window: {window_id: request.report_window_id, subscription_revision: request.report_subscription_revision,
      goal_id: p.actor.goal_id, agent_id: p.actor.agent_id},
    report_subscription: {enabled: true, profile_preset: "portfolio-progress",
      route_ref: `steward-private:${p.binding.binding_id}:${p.source}`, effective_revision: request.report_subscription_revision},
    report_todo: {todo_id: request.todo_id, target_key: request.report_window_id,
      claimed_by: "steward", action_kind: "generate_periodic_report", status: "open"}};
  assert.equal(admitStewardManagedTurn(input).observation_only, false);
  for (const original of [
    {...source, status: "failed"}, {...source, delta_count: 1}, {...source, upstream_turn_id: "actual-model"},
    {...source, session_id: "other"}, {...source, loopx_request: {...request, todo_id: "todo_other"}},
    ...["host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"].flatMap(key => [true, null].map(value =>
      ({...source, response: {native_loopx: {...result, effects: {...effects, [key]: value}}}}))),
    ...[{state: "unavailable"}, {reason_code: "control_repair"}, {status_health_ok: false}, {contract_error_count: 1},
      {recovery_action: "inspect_original"}, {requested_todo_id: "todo_other"}].map(changed =>
      ({...source, response: {native_loopx: {...result, selection_rejection: {...refusal, ...changed}}}})),
    {...source, response: {native_loopx: {...result, resume_turn_key: "sha256:" + "a".repeat(64)}}},
  ]) assert.throws(() => admitStewardManagedTurn({...input, report_selection_recovery: {...proof, original}}));
  for (const changed of [
    {report_selection_recovery: {...proof, generation_present: true}},
    {report_subscription: {...input.report_subscription, enabled: false}}, {queued_turn_count: 1}, {goal_active: false},
    {request: {...request, work_kind: "native_turn"}},
  ]) assert.throws(() => admitStewardManagedTurn({...input, ...changed}));
});
