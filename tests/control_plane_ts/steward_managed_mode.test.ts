import assert from "node:assert/strict";
import test from "node:test";
import {admitStewardManagedTurn, bindStewardManagedActor, observeStewardNativeExecutor, planStewardManagedMode,
  planStewardMonitor, observeStewardPortfolio,
  observeStewardSignalMonitor} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";
import {planConversationBinding} from "../../loopx/control_plane/collaboration/conversation_binding.ts";

function fixture() {
  const binding = {schema_version: "loopx_chat_conversation_binding_v0", binding_id: "b".repeat(24),
    transport_ref: "steward-app", provider_ref: "c".repeat(24), operator_ref: "d".repeat(24),
    project_ref: "a".repeat(24), context_kind: "steward", grant: "portfolio_read",
    executor_endpoint_id: "codex", enabled: true, goal_scope: "all_registered", goal_ids: []};
  const source = "e".repeat(24);
  const session = {session_id: "f".repeat(32), agent_id: "codex", goal_id: "loopx-manager", status: "ready",
    upstream_thread_id: "original-thread", channel_id: `manager.external.native.${binding.binding_id}.${source}`,
    steward_context: {binding_id: binding.binding_id, provider_ref: binding.provider_ref,
      operator_ref: binding.operator_ref, project_ref: binding.project_ref, source_ref: source}};
  const params = {binding, session, binding_revision: 4, queued_turn_count: 0,
    observation: {transport_ref: binding.transport_ref, provider_ref: binding.provider_ref,
      operator_ref: binding.operator_ref, verified: true}, workspace_path: "/runtime/chat/manager-workspaces/original"};
  const goal = {goal_id: `steward-agent-${binding.binding_id}`, goal_instance_id: "original-goal-lifetime",
    workspace_path: params.workspace_path, registered_agents: ["steward"],
    creation_operation_id: `steward-managed:${binding.binding_id}:${source}`};
  return {params, goal};
}

test("only the canonical open watch can run without a model, and it cannot reuse another model scope", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const request = {operation: "native_turn", host: "codex-app-server", actor,
    work_kind: "monitor", turn_instance_id: "monitor.original", todo_id: "todo_watch", validation_command: null,
    monitor_deadline: "2026-10-23T10:00:00Z", monitor_lease_version: 0};
  const monitor = {todo_id: "todo_watch", task_class: "continuous_monitor", target_key: "steward-portfolio-watch",
    watch_only: "true", claimed_by: "steward", status: "open", next_due_at: request.monitor_deadline};
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor}, request,
    monitor_todo: monitor, session: {...params.session, active_turn_id: "original-watch", manager_authorization_scope_id: "old-model-scope"},
    scope: {current: "new-authorized-goals", without_actor: "also-new"},
    turn: {session_id: params.session.session_id, turn_id: "original-watch", status: "running",
      origin: "native_loopx", loopx_execution: true, loopx_request: request}};
  const admitted = admitStewardManagedTurn(input);
  assert.equal(admitted.observation_only, true);
  assert.equal(admitted.refresh_original_scope, false); // the model's isolation hash is preserved
  for (const invalid of [null, {...monitor, status: "blocked"}, {...monitor, watch_only: false},
    {...monitor, claimed_by: "other-owner"}, {...monitor, todo_id: "todo_other"}, {...monitor, next_due_at: "later"}]) {
    assert.throws(() => admitStewardManagedTurn({...input, monitor_todo: invalid}));
  }
  const modelRequest = {...request, work_kind: "native_turn"};
  assert.throws(() => admitStewardManagedTurn({...input, request: modelRequest,
    turn: {...input.turn, loopx_request: modelRequest}}));
});

test("identity-refused watches need independent collector evidence and retain current admission", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const prior = {operation: "native_turn", host: "codex-app-server", actor,
    work_kind: "monitor", turn_instance_id: "monitor.original", todo_id: "todo_watch", validation_command: null,
    monitor_deadline: "2026-10-23T10:00:00Z", monitor_lease_version: 0};
  const original = {turn_id: "1".repeat(32), session_id: params.session.session_id,
    status: "failed", error_code: "runtime_error", error: "the selected App identity could not be verified",
    origin: "native_loopx", loopx_execution: true, upstream_turn_id: null, response: null,
    delta_count: 0, loopx_request: prior};
  const basis = {original, contexts: [], phases: [{phase: "manager_context"}],
    event_kinds: ["turn.queued", "agent.phase", "turn.failed"],
    poll_absent: true, old_worker_excluded: true, lease_available: true};
  const monitor = {todo_id: "todo_watch", task_class: "continuous_monitor", target_key: "steward-portfolio-watch",
    watch_only: true, claimed_by: "steward", status: "open", next_due_at: prior.monitor_deadline};
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
    request: {...prior, monitor_restart_recovery: {original_turn_id: original.turn_id}},
    monitor_todo: monitor, monitor_restart_basis: basis};
  assert.equal(admitStewardManagedTurn(input).observation_only, true);
  for (const changed of [
    {phases: []}, {phases: [{phase: "model_start"}]}, {phases: [{phase: "manager_context"}, {phase: "manager_context"}]},
    {contexts: [{}]}, {event_kinds: ["turn.queued", "turn.failed"]},
    {event_kinds: [...basis.event_kinds, "agent.phase"]},
    {poll_absent: false}, {old_worker_excluded: false}, {lease_available: false},
    {original: {...original, error: "Unknown identity failure"}},
    {original: {...original, upstream_turn_id: "unknown-model-turn"}},
  ]) assert.throws(() => admitStewardManagedTurn({...input, monitor_restart_basis: {...basis, ...changed}}));
  for (const changed of [{queued_turn_count: 1}, {goal_active: false},
    {monitor_todo: {...monitor, status: "blocked"}}, {observation: {...params.observation, verified: false}}]) {
    assert.throws(() => admitStewardManagedTurn({...input, ...changed}));
  }
});

test("signal-unavailable custom monitors admit exactly one zero-effect recovery after manager_context", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const prior = {operation: "native_turn", host: "codex-app-server", actor,
    work_kind: "monitor", turn_instance_id: "monitor.signal", todo_id: "todo_signal_watch", validation_command: null,
    monitor_deadline: "2026-11-19T13:21:54Z", monitor_lease_version: 0};
  const original = {turn_id: "2".repeat(32), session_id: params.session.session_id,
    status: "failed", error_code: "runtime_error",
    error: "steward monitor external signal source temporarily unavailable",
    origin: "native_loopx", loopx_execution: true, upstream_turn_id: null, response: null,
    delta_count: 0, loopx_request: prior};
  const basis = {original, contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
    phases: [{phase: "manager_context"}],
    event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
    poll_absent: true, old_worker_excluded: true, lease_available: true};
  const monitor = {todo_id: "todo_signal_watch", task_class: "continuous_monitor",
    target_key: "steward-stalled-pr-monitor", claimed_by: "steward", status: "open",
    next_due_at: prior.monitor_deadline};
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
    request: {...prior, monitor_restart_recovery: {original_turn_id: original.turn_id}},
    monitor_todo: monitor, monitor_restart_basis: basis};
  assert.equal(admitStewardManagedTurn(input).observation_only, true);

  for (const changed of [
    {contexts: []},
    {contexts: [{schema_version: "wrong_schema"}]},
    {contexts: [...basis.contexts, ...basis.contexts]},
    {phases: [{phase: "model_start"}]},
    {event_kinds: ["turn.queued", "agent.phase", "turn.failed"]},
    {poll_absent: false},
    {lease_available: false},
    {original: {...original, error: "other transient failure"}},
    {original: {...original, upstream_turn_id: "model-started"}},
    {original: {...original, loopx_request: {...prior, monitor_restart_recovery: {original_turn_id: "3".repeat(32)}}}},
  ]) {
    assert.throws(() => admitStewardManagedTurn({...input, monitor_restart_basis: {...basis, ...changed}}));
  }
});

test("monitor configuration retains the original actor and native Todo authority", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
    operation: "enable", cadence_minutes: 30, todos: []};
  const plan = planStewardMonitor(input);
  assert.deepEqual(plan.actor, actor);
  assert.equal(plan.model_invoked, false);
  const todo = {todo_id: "todo_original", task_class: "continuous_monitor", target_key: "steward-portfolio-watch",
    watch_only: true, claimed_by: "steward", status: "blocked"};
  assert.notEqual(planStewardMonitor({...input, todos: [todo]}).plan_id, plan.plan_id);
  for (const invalid of [
    {...input, cadence_minutes: 1}, {...input, cadence_minutes: 30.5}, {...input, goal_active: false},
    {...input, todos: [todo, todo]}, {...input, todos: [{...todo, claimed_by: "another-owner"}]},
    {...input, todos: [{...todo, status: "done"}]},
  ]) assert.throws(() => planStewardMonitor(invalid));
});

test("observations retain stopped Goals and gaps, distinguish material changes, and ignore collection clocks", () => {
  const original = {goal_id: "original", activation_state: "stopped", quality: "unavailable",
    attention: [{kind: "pending-return", request_ref: "retained-original"}], progress: {completed: 1},
    agents: [{agent_id: "original-agent", age_seconds: 1}], generated_at: "first"};
  const own = {goal_id: "steward", progress: {monitor_polls: 1}};
  const input = {context: {goals: [original, own], coverage: {gaps: ["one-source-unavailable"]}},
    goal_ids: ["steward", "original"], actor_goal_id: "steward", previous_hash: null};
  const first = observeStewardPortfolio(input);
  assert.equal(first.observed_goal_count, 2);
  assert.equal(first.material_change, false);
  assert.deepEqual(first.coverage, input.context.coverage);
  const unchanged = observeStewardPortfolio({...input, previous_hash: first.result_hash,
    context: {...input.context, goals: [{...own, progress: {monitor_polls: 2}},
      {...original, generated_at: "second", agents: [{agent_id: "original-agent", age_seconds: 88}]}]}});
  assert.equal(unchanged.result_hash, first.result_hash);
  assert.equal(unchanged.material_change, false);
  assert.equal(observeStewardPortfolio({...input, previous_hash: first.result_hash,
    context: {...input.context, goals: [{...original, progress: {completed: 2}}, own]}}).material_change, true);
  assert.throws(() => observeStewardPortfolio({...input, context: {goals: [own]}}));
  assert.throws(() => observeStewardPortfolio({...input, context: {goals: [own, original, original]}}));
});

test("native dispatch binds the prepared actor, while an active original Turn permits revalidation only", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const request = {operation: "native_turn", host: "codex-app-server", actor,
    turn_instance_id: "monitor-window-1", todo_id: "todo_original", validation_command: null};
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor}, request};
  assert.deepEqual(admitStewardManagedTurn(input).actor, actor);
  const active = {...input, session: {...params.session, active_turn_id: "accepted-native", manager_authorization_scope_id: "prior-scope"},
    scope: {current: "new-scope", without_actor: "prior-scope"},
    turn: {turn_id: "accepted-native", session_id: params.session.session_id, status: "running",
      origin: "native_loopx", loopx_execution: true, loopx_request: request}};
  assert.deepEqual(admitStewardManagedTurn({...active, queued_turn_count: 1}).actor, actor);
  assert.equal(admitStewardManagedTurn(active).refresh_original_scope, true);
  assert.throws(() => admitStewardManagedTurn({...input, queued_turn_count: 1}));
  assert.throws(() => admitStewardManagedTurn({...input, session: active.session}));
  for (const changed of [
    {...active, turn: {...active.turn, status: "interrupted"}},
    {...active, turn: {...active.turn, origin: "lark"}},
    {...active, turn: {...active.turn, loopx_execution: false}},
    {...active, turn: {...active.turn, turn_id: "other-turn"}},
    {...active, request: {...request, actor: {...actor, source_ref: "0".repeat(24)}}},
    {...active, request: {...request, host: "codex-cli"}},
    {...active, goal_active: false},
    {...active, goal: {...goal, goal_instance_id: "replacement-lifetime"}},
    {...active, scope: {current: "other-scope", without_actor: "not-the-original-scope"}},
    {...active, binding: {...active.binding, goal_scope: "selected"}},
    {...active, request: {...request, validation_command: ["shell\0injection"]}},
  ]) assert.throws(() => admitStewardManagedTurn(changed));
});

test("preparation names one persistent steward, without model or scheduling authority", () => {
  const {params, goal} = fixture();
  const before = structuredClone(params);
  const first = planStewardManagedMode(params);
  assert.deepEqual(params, before);
  assert.equal(first.goal_id, goal.goal_id);
  assert.equal(first.agent_id, "steward");
  assert.equal(first.session_id, params.session.session_id);
  assert.equal(first.model_invoked, false);
  assert.equal(first.scheduling_enabled, false);
  assert.equal(first.execution_authority_granted, false);
  // An exact creation's response can be lost before binding; recover the
  // same consent basis instead of requiring a second Goal or model.
  assert.equal(planStewardManagedMode({...params, goal}).plan_id, first.plan_id);
  assert.notEqual(planStewardManagedMode({...params, binding_revision: 5}).plan_id, first.plan_id);
});

test("ordinary, group, attached and foreign-source conversations cannot acquire a steward actor", () => {
  const {params} = fixture();
  for (const input of [
    {...params, binding: {...params.binding, context_kind: "project", grant: "workspace_write"}},
    {...params, binding: {...params.binding, audience: "group"}},
    {...params, session: {...params.session, session_mode: "attached_host"}},
    {...params, session: {...params.session, goal_id: "another-goal"}},
    {...params, session: {...params.session, channel_id: "manager.external.other"}},
    {...params, session: {...params.session, steward_context: {...params.session.steward_context, operator_ref: "0".repeat(24)}}},
  ]) assert.throws(() => planStewardManagedMode(input));
});

test("busy work, an ordered user queue and unverified provider identity stop preparation", () => {
  const {params} = fixture();
  for (const input of [
    {...params, session: {...params.session, active_turn_id: "accepted-original"}},
    {...params, queued_turn_count: 1},
    {...params, observation: {...params.observation, verified: false}},
    {...params, observation: {...params.observation, provider_ref: "0".repeat(24)}},
  ]) assert.throws(() => planStewardManagedMode(input));
});

test("only the original native creation, workspace and registered Agent establish a binding", () => {
  const {params, goal} = fixture();
  for (const invalid of [undefined, {...goal, creation_operation_id: "other-operation"},
    {...goal, workspace_path: "/other-project"}, {...goal, goal_id: "other-goal"},
    {...goal, registered_agents: ["project-worker"]}]) {
    assert.throws(() => bindStewardManagedActor({...params, goal: invalid}));
  }
  const actor = bindStewardManagedActor({...params, goal});
  assert.equal(actor.goal_instance_id, goal.goal_instance_id);
  assert.equal(actor.session_id, params.session.session_id);
  assert.throws(() => planStewardManagedMode({...params, binding: {...params.binding, managed_actor: actor},
    goal: {...goal, goal_instance_id: "replacement-lifetime"}}));
  assert.throws(() => planStewardManagedMode({...params, binding: {...params.binding, managed_actor: actor}}));
  assert.throws(() => planStewardManagedMode({...params, binding: {...params.binding, managed_actor: actor},
    goal: {...goal, registered_agents: []}}));
});

test("publication changes only the native actor reference; scope configuration retains it", () => {
  const {params, goal} = fixture();
  const current = {schema_version: "loopx_chat_conversation_bindings_v0", revision: 4, bindings: [params.binding]};
  const result = planConversationBinding({...params, goal, current, expected_revision: 4,
    binding_id: params.binding.binding_id, operation: "bind_managed_actor"});
  const next = result.state as typeof current;
  const prepared = next.bindings[0] as typeof params.binding & {managed_actor: Record<string, unknown>};
  assert.deepEqual({...prepared, managed_actor: undefined}, {...params.binding, managed_actor: undefined});
  assert.equal(next.revision, 5);
  const replay = planConversationBinding({...params, goal, binding: prepared, current: next,
    binding_id: prepared.binding_id, expected_revision: 5, operation: "bind_managed_actor"});
  assert.equal(replay.changed, false);
  const configured = planConversationBinding({current: next, expected_revision: 5, operation: "configure",
    binding: {...prepared, goal_scope: "selected"}, observation: params.observation,
    available_projects: [{kind: "project_workspace", project_ref: prepared.project_ref,
      workspace_path: "/authorized/workspace", audience: "local_owner", grant: "workspace_read"}]});
  assert.deepEqual((configured.state as typeof next).bindings[0].managed_actor, prepared.managed_actor);
  assert.throws(() => planConversationBinding({current: next, expected_revision: 5, operation: "configure",
    binding: {...prepared, context_kind: "project", grant: "workspace_read"}, observation: params.observation}));
});

test("custom steward signal monitors admit model-free probes while excluding local-session watches", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const request = {operation: "native_turn", host: "codex-app-server", actor,
    work_kind: "monitor", turn_instance_id: "monitor.custom", todo_id: "todo_pr_watch", validation_command: null,
    monitor_deadline: "2026-11-19T12:00:00Z", monitor_lease_version: 0};
  const customMonitor = {todo_id: "todo_pr_watch", task_class: "continuous_monitor",
    target_key: "steward-stalled-pr-monitor", claimed_by: "steward", status: "open",
    next_due_at: request.monitor_deadline};
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
    request, monitor_todo: customMonitor};
  assert.equal(admitStewardManagedTurn(input).observation_only, true);
  for (const excludedKey of ["managed-session-watch." + "0".repeat(32), "steward-monitor-followup:todo_1", "bad key!"]) {
    assert.throws(() => admitStewardManagedTurn({
      ...input,
      monitor_todo: {...customMonitor, target_key: excludedKey},
    }));
  }

  const prItem = {number: 9024, headRefOid: "abc123", reviewDecision: "CHANGES_REQUESTED"};
  const first = observeStewardSignalMonitor({
    portfolio_observation: {result_hash: "sha256:portfolio1"},
    inbox_items: [{request_id: "req_1", source_id: "peer"}],
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [prItem], observed_at: "t1"},
    previous_hash: null,
  });
  assert.equal(first.material_change, true);
  assert.equal(first.observation_incomplete, false);

  const unchanged = observeStewardSignalMonitor({
    portfolio_observation: {result_hash: "sha256:portfolio1"},
    inbox_items: [{request_id: "req_1", source_id: "peer"}],
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [prItem], observed_at: "t2", collected_at: "later"},
    previous_hash: first.result_hash,
  });
  assert.equal(unchanged.result_hash, first.result_hash);
  assert.equal(unchanged.material_change, false);

  const changed = observeStewardSignalMonitor({
    portfolio_observation: {result_hash: "sha256:portfolio1"},
    inbox_items: [{request_id: "req_1", source_id: "peer"}],
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [{...prItem, headRefOid: "def456"}]},
    previous_hash: first.result_hash,
  });
  assert.equal(changed.material_change, true);
  assert.notEqual(changed.result_hash, first.result_hash);

  const unavailable = observeStewardSignalMonitor({
    portfolio_observation: {result_hash: "sha256:portfolio1"},
    inbox_items: [{request_id: "req_1", source_id: "peer"}],
    repository_snapshot: {status: "unavailable", repository: "acme/widgets"},
    previous_hash: first.result_hash,
  });
  assert.equal(unavailable.observation_incomplete, true);
  assert.equal(unavailable.material_change, false);
  assert.equal(unavailable.result_hash, first.result_hash);
});

test("observeStewardNativeExecutor admits qualified codex-to-antigravity fallback while rejecting unallocated antigravity", () => {
  const {params} = fixture();
  assert.equal(observeStewardNativeExecutor(params).execution_available, true);
  const pureAntigravity = {
    binding: {...params.binding, executor_endpoint_id: "antigravity"},
    session: {...params.session, agent_id: "antigravity", executor_endpoint_id: "antigravity"},
  };
  assert.deepEqual(observeStewardNativeExecutor(pureAntigravity), {
    execution_available: false,
    execution_error_code: "unsupported_executor",
  });
  const qualifiedFallback = {
    binding: params.binding,
    session: {
      ...params.session,
      agent_id: "antigravity",
      executor_endpoint_id: "antigravity",
      upstream_context_history: [{agent_id: "codex", thread_id: params.session.upstream_thread_id}],
      manager_executor_allocation: {
        schema_version: "manager_executor_allocation_v0",
        selection_policy: "flexible",
        configured_endpoint: "codex",
        executor_endpoint: "antigravity",
        allocation_reason: "flexible_availability_fallback",
        configuration_revision: "rev-1",
        eligible_endpoints: ["codex", "antigravity"],
      },
    },
  };
  assert.equal(observeStewardNativeExecutor(qualifiedFallback).execution_available, true);
  assert.equal(observeStewardNativeExecutor({
    ...qualifiedFallback,
    session: {...qualifiedFallback.session, upstream_context_history: []},
  }).execution_available, false);
  const flexiblePrimaryMigrated = {
    binding: params.binding,
    session: {
      ...qualifiedFallback.session,
      manager_executor_allocation: {
        schema_version: "manager_executor_allocation_v0",
        selection_policy: "flexible",
        configured_endpoint: "antigravity",
        executor_endpoint: "antigravity",
        allocation_reason: "flexible_primary_available",
        configuration_revision: "rev-2",
        eligible_endpoints: ["antigravity", "codex"],
      },
    },
  };
  assert.equal(observeStewardNativeExecutor(flexiblePrimaryMigrated).execution_available, true);
});

test("zero-effect worker exit, storage I/O, and runtime unavailability monitors admit one bounded recovery", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const prior = {operation: "native_turn", host: "codex-app-server", actor,
    work_kind: "monitor", turn_instance_id: "monitor.transient", todo_id: "todo_transient_watch", validation_command: null,
    monitor_deadline: "2026-11-20T07:44:39Z", monitor_lease_version: 0};
  const monitor = {todo_id: "todo_transient_watch", task_class: "continuous_monitor",
    target_key: "steward-portfolio-watch", watch_only: true, claimed_by: "steward", status: "open",
    next_due_at: prior.monitor_deadline};
  const workerExitedError =
    "The LoopX Chat execution worker exited before this turn finished. Your conversation is saved; execution may have occurred. This turn was not retried automatically.";
  for (const scenario of [
    {
      error_code: "runtime_error",
      error: workerExitedError,
      contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
    },
    {
      error_code: "runtime_error",
      error: "disk I/O error",
      contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
    },
    {
      error_code: "runtime_unavailable",
      error: "The Agent runtime could not be prepared.",
      contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      event_kinds: ["turn.queued", "turn.failed", "agent.phase", "manager.context"],
    },
  ]) {
    const original = {turn_id: "4".repeat(32), session_id: params.session.session_id,
      status: "failed", error_code: scenario.error_code, error: scenario.error,
      origin: "native_loopx", loopx_execution: true, upstream_turn_id: null, response: null,
      delta_count: 0, loopx_request: prior};
    const basis = {original, contexts: scenario.contexts, phases: [{phase: "manager_context"}],
      event_kinds: scenario.event_kinds, poll_absent: true, old_worker_excluded: true, lease_available: true};
    const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
      request: {...prior, monitor_restart_recovery: {original_turn_id: original.turn_id}},
      monitor_todo: monitor, monitor_restart_basis: basis};
    assert.equal(admitStewardManagedTurn(input).observation_only, true);
    for (const bad of [
      {contexts: []},
      {phases: []},
      {contexts: [{schema_version: "wrong"}]},
      {contexts: [{schema_version: "manager_turn_context_v1"}, {schema_version: "manager_turn_context_v1"}]},
      {poll_absent: false},
      {old_worker_excluded: false},
      {lease_available: false},
      {original: {...original, upstream_turn_id: "started-upstream"}},
      {original: {...original, loopx_request: {...prior, monitor_restart_recovery: {original_turn_id: "5".repeat(32)}}}},
    ]) {
      assert.throws(() => admitStewardManagedTurn({...input, monitor_restart_basis: {...basis, ...bad}}));
    }
  }
});

test("chained post-context transient watch carriers recover only after cooldown while scope and identity refusals remain one-shot", () => {
  const {params, goal} = fixture();
  const actor = bindStewardManagedActor({...params, goal});
  const baseRequest = {operation: "native_turn", host: "codex-app-server", actor,
    work_kind: "monitor", turn_instance_id: "monitor.chained", todo_id: "todo_chained_watch", validation_command: null,
    monitor_deadline: "2026-11-20T07:45:00Z", monitor_lease_version: 0};
  const completedAt = "2026-11-20T07:45:00Z";
  const completedSec = Date.parse(completedAt) / 1000;
  const monitor = {todo_id: "todo_chained_watch", task_class: "continuous_monitor",
    target_key: "steward-portfolio-watch", watch_only: true, claimed_by: "steward", status: "open",
    next_due_at: baseRequest.monitor_deadline};
  const carrier = {turn_id: "6".repeat(32), session_id: params.session.session_id,
    status: "failed", error_code: "runtime_unavailable",
    error: "The Agent runtime could not be prepared.",
    origin: "native_loopx", loopx_execution: true, upstream_turn_id: null, response: null,
    delta_count: 0, completed_at: completedAt,
    loopx_request: {...baseRequest, monitor_restart_recovery: {original_turn_id: "5".repeat(32)}}};
  const basis = {original: carrier, contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
    phases: [{phase: "manager_context"}],
    event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
    poll_absent: true, old_worker_excluded: true, lease_available: true, observed_at: completedSec + 300};
  const input = {...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
    request: {...baseRequest, monitor_restart_recovery: {original_turn_id: carrier.turn_id}},
    monitor_todo: monitor, monitor_restart_basis: basis};
  assert.equal(admitStewardManagedTurn(input).observation_only, true);

  for (const badBasis of [
    {observed_at: completedSec + 299},
    {observed_at: undefined},
    {original: {...carrier, completed_at: undefined}},
    {original: {...carrier, error_code: "runtime_error", error: "the portfolio observation omitted authorized Goals",
      contexts: [{schema_version: "manager_turn_context_v1", goals: [], warnings: ["external_authorization_changed"],
        coverage: {discovered: null, verified: 0, complete: false}}]}, observed_at: completedSec + 600},
    {original: {...carrier, error_code: "runtime_error", error: "the selected App identity could not be verified"},
      contexts: [], event_kinds: ["turn.queued", "agent.phase", "turn.failed"], observed_at: completedSec + 600},
  ]) {
    assert.throws(() => admitStewardManagedTurn({...input, monitor_restart_basis: {...basis, ...badBasis}}));
  }
});
