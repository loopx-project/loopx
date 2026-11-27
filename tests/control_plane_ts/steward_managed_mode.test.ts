import assert from "node:assert/strict";
import test from "node:test";
import {admitStewardManagedTurn, bindStewardManagedActor, observeStewardNativeExecutor, planStewardManagedMode,
  planStewardMonitor, observeStewardPortfolio,
  observeStewardSignalMonitor, planStewardSignalCommentBackfill} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";
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
  assert.equal(
    admitStewardManagedTurn({
      ...input,
      monitor_restart_basis: {
        ...basis,
        original: {...original, error: "the portfolio scope changed after observation"},
      },
    }).observation_only,
    true,
  );

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
    {
      error_code: "lock_acquire_timeout",
      error: "file lock acquisition timed out (recorded holder pid 4189)",
      contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
    },
    {
      error_code: "runtime_error",
      error: "file lock acquisition timed out (recorded holder pid 4189)",
      contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
    },
    {
      error_code: "runtime_error",
      error: "file lock acquisition timed out (lock busy, current holder unknown)",
      contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
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
  for (const badLockError of [
    "file lock acquisition timed out (recorded holder pid 0)",
    "file lock acquisition timed out (arbitrary detail)",
    "prefix file lock acquisition timed out (recorded holder pid 4189)",
  ]) {
    const original = {turn_id: "4".repeat(32), session_id: params.session.session_id,
      status: "failed", error_code: "runtime_error", error: badLockError,
      origin: "native_loopx", loopx_execution: true, upstream_turn_id: null, response: null,
      delta_count: 0, loopx_request: prior};
    const basis = {original, contexts: [{schema_version: "manager_turn_context_v1", goals: [goal]}],
      phases: [{phase: "manager_context"}],
      event_kinds: ["turn.queued", "agent.phase", "manager.context", "turn.failed"],
      poll_absent: true, old_worker_excluded: true, lease_available: true};
    assert.throws(() => admitStewardManagedTurn({
      ...params, goal, goal_active: true, binding: {...params.binding, managed_actor: actor},
      request: {...prior, monitor_restart_recovery: {original_turn_id: original.turn_id}},
      monitor_todo: monitor, monitor_restart_basis: basis,
    }));
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
  for (const [errorCode, errorText] of [
    ["lock_acquire_timeout", "file lock acquisition timed out (recorded holder pid 4189)"],
    ["runtime_error", "file lock acquisition timed out (recorded holder pid 4189)"],
  ] as const) {
    const lockCarrier = {...carrier, error_code: errorCode, error: errorText};
    assert.equal(admitStewardManagedTurn({
      ...input, monitor_restart_basis: {...basis, original: lockCarrier},
    }).observation_only, true);
    assert.throws(() => admitStewardManagedTurn({
      ...input, monitor_restart_basis: {...basis, original: lockCarrier, observed_at: completedSec + 299},
    }));
  }

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

test("observeStewardSignalMonitor preserves feature-off parity and isolates scoped PR monitor hashes", () => {
  const sampleRepo = {
    status: "ok",
    repository: "acme/widgets",
    items: [
      {
        number: 101,
        title: "Mergeable GPT-approved PR",
        isDraft: false,
        mergeable: "MERGEABLE",
        author: {login: "alice"},
        updatedAt: "2026-11-20T09:00:00Z",
        review_summary: {
          effective_verdict: "APPROVE",
          has_review_conflict: false,
          exact_head_reviews: [
            {author_login: "bob", reviewer_declaration: "GPT-5.4", verdict: "APPROVE", valid_verdict_review: true, state: "APPROVED"},
          ],
        },
      },
      {
        number: 102,
        title: "Dismissed GPT review then author-owned fallback only",
        isDraft: false,
        mergeable: "MERGEABLE",
        author: {login: "alice"},
        updatedAt: "2026-11-18T00:00:00Z",
        review_summary: {
          effective_verdict: "APPROVE",
          has_review_conflict: false,
          exact_head_reviews: [
            {author_login: "bob", reviewer_declaration: "GPT-5.4", verdict: "APPROVE", valid_verdict_review: true, state: "APPROVED"},
            {author_login: "bob", reviewer_declaration: "GPT-5.4", verdict: null, valid_verdict_review: false, state: "DISMISSED"},
            {author_login: "alice", reviewer_declaration: "Codex Author Fallback", verdict: "APPROVE", valid_verdict_review: true, state: "COMMENTED", author_owned_fallback: true},
          ],
        },
      },
      {
        number: 103,
        title: "External stalled PR",
        isDraft: false,
        mergeable: "CONFLICTING",
        author: {login: "ext-contributor"},
        updatedAt: "2026-11-19T08:00:00Z",
        review_summary: {effective_verdict: "CHANGES_REQUESTED", has_review_conflict: false, exact_head_reviews: []},
      },
      {
        number: 104,
        title: "Draft PR excluded",
        isDraft: true,
        mergeable: "MERGEABLE",
        author: {login: "alice"},
        updatedAt: "2026-11-18T00:00:00Z",
        review_summary: {effective_verdict: "APPROVE", has_review_conflict: false, exact_head_reviews: []},
      },
    ],
  };

  // Feature-off parity: null and undefined signal_scope behave identically and omit scoped_repository_items.
  const legacyUndef = observeStewardSignalMonitor({
    portfolio_observation: {result_hash: "p1"},
    inbox_items: [{request_id: "r1"}],
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T08:00:00Z",
  });
  const legacyNull = observeStewardSignalMonitor({
    signal_scope: null,
    portfolio_observation: {result_hash: "p1"},
    inbox_items: [{request_id: "r1"}],
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T08:00:00Z",
  });
  assert.equal(legacyUndef.result_hash, legacyNull.result_hash);
  assert.equal("scoped_repository_items" in legacyUndef, false);

  const mergeableScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      exclude_conflicting: true,
      effective_verdicts: ["APPROVE"],
      required_approver_declarations: ["gpt", "openai"],
    },
  };
  const internalStalledScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      author_mode: "include",
      authors: ["Alice"],
      min_stalled_hours: 24,
    },
  };
  const externalStalledScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      author_mode: "exclude",
      authors: ["alice"],
      min_stalled_hours: 24,
    },
  };

  // Mergeable monitor matches only PR #101 (PR #102's GPT review was DISMISSED).
  const mergeableObs = observeStewardSignalMonitor({
    signal_scope: mergeableScope,
    portfolio_observation: {result_hash: "p1"},
    inbox_items: [{request_id: "r1"}],
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T08:00:00Z",
  });
  assert.deepEqual(mergeableObs.scoped_repository_items.map((row: {number: number}) => row.number), [101]);

  // Changing portfolio_observation and inbox_items is ignored when include_portfolio/include_inbox are false.
  const mergeableObsPortfolioMutated = observeStewardSignalMonitor({
    signal_scope: mergeableScope,
    previous_hash: mergeableObs.result_hash,
    portfolio_observation: {result_hash: "p2-changed"},
    inbox_items: [{request_id: "r1"}, {request_id: "r2"}],
    repository_snapshot: {
      ...sampleRepo,
      items: [
        sampleRepo.items[0],
        sampleRepo.items[1],
        {...sampleRepo.items[2], title: "Unrelated external update"},
        sampleRepo.items[3],
      ],
    },
    observed_at: "2026-11-21T08:00:00Z",
  });
  assert.equal(mergeableObsPortfolioMutated.result_hash, mergeableObs.result_hash);
  assert.equal(mergeableObsPortfolioMutated.material_change, false);

  // Stalled threshold flips once at >= 24h and stays stable at 25h / 48h.
  // PR #101 updatedAt is 2026-11-20T09:00:00Z; #102 is 2026-11-18T00:00:00Z.
  const at23h = observeStewardSignalMonitor({
    signal_scope: internalStalledScope,
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T08:00:00Z", // #101 is 23h old -> only #102 matches
  });
  assert.deepEqual(at23h.scoped_repository_items.map((row: {number: number}) => row.number), [102]);
  assert.equal(at23h.scoped_repository_items[0].stalled_threshold_met, true);

  const at24h = observeStewardSignalMonitor({
    signal_scope: internalStalledScope,
    previous_hash: at23h.result_hash,
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T09:00:00Z", // #101 reaches 24h -> flips once
  });
  assert.deepEqual(at24h.scoped_repository_items.map((row: {number: number}) => row.number), [101, 102]);
  assert.equal(at24h.material_change, true);

  const at25h = observeStewardSignalMonitor({
    signal_scope: internalStalledScope,
    previous_hash: at24h.result_hash,
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T10:00:00Z", // 25h -> stable hash, no re-wake
  });
  assert.equal(at25h.result_hash, at24h.result_hash);
  assert.equal(at25h.material_change, false);

  const externalObs = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    repository_snapshot: sampleRepo,
    observed_at: "2026-11-21T08:00:00Z",
  });
  assert.deepEqual(externalObs.scoped_repository_items.map((row: {number: number}) => row.number), [103]);
  assert.notEqual(mergeableObs.result_hash, at24h.result_hash);
  assert.notEqual(at24h.result_hash, externalObs.result_hash);

  // Domain-neutral filter rejects legacy vendor-specific field and empty declaration lists.
  assert.throws(() => observeStewardSignalMonitor({
    signal_scope: {
      schema_version: "steward_signal_monitor_scope_v0",
      pr_filter: {require_independent_gpt_approve: true},
    },
    repository_snapshot: sampleRepo,
  }));
  assert.throws(() => observeStewardSignalMonitor({
    signal_scope: {
      schema_version: "steward_signal_monitor_scope_v0",
      pr_filter: {required_approver_declarations: []},
    },
    repository_snapshot: sampleRepo,
  }));
  assert.throws(() => observeStewardSignalMonitor({
    signal_scope: internalStalledScope,
    repository_snapshot: sampleRepo,
  }));
});

test("observeStewardSignalMonitor uses stalled_since, author-reply bypass, and stalled_bucket wakeups", () => {
  const stalledInternalScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "include",
      authors: ["alice"],
      min_stalled_hours: 24,
    },
  };
  const stalledExternalScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "exclude",
      authors: ["alice"],
      min_stalled_hours: 24,
    },
  };

  const makeRepo = (botUpdatedAt: string) => ({
    status: "ok",
    repository: "acme/widgets",
    items: [
      {
        number: 201,
        title: "Internal RC with Mergify bot ping",
        isDraft: false,
        mergeable: "MERGEABLE",
        author: {login: "alice"},
        updatedAt: botUpdatedAt,
        stalled_since: "2026-11-20T09:00:00Z",
        author_replied_after_request_changes: false,
        review_summary: {
          effective_verdict: "REQUEST_CHANGES",
          has_review_conflict: false,
          latest_request_changes_review_id: "PRR_201",
          stalled_since: "2026-11-20T09:00:00Z",
          author_replied_after_request_changes: false,
          exact_head_reviews: [],
        },
      },
      {
        number: 202,
        title: "External author replied right after RC",
        isDraft: false,
        mergeable: "MERGEABLE",
        author: {login: "ext-user"},
        updatedAt: "2026-11-21T08:30:00Z",
        stalled_since: "2026-11-21T07:00:00Z",
        author_replied_after_request_changes: true,
        review_summary: {
          effective_verdict: "REQUEST_CHANGES",
          has_review_conflict: false,
          latest_request_changes_review_id: "PRR_202",
          stalled_since: "2026-11-21T07:00:00Z",
          author_replied_after_request_changes: true,
          exact_head_reviews: [],
        },
      },
      {
        number: 203,
        title: "Stale RC on previous commit after new head push",
        isDraft: false,
        mergeable: "MERGEABLE",
        author: {login: "ext-user"},
        updatedAt: "2026-11-18T00:00:00Z",
        stalled_since: null,
        author_replied_after_request_changes: false,
        review_summary: {
          effective_verdict: "REQUEST_CHANGES",
          has_review_conflict: false,
          latest_request_changes_review_id: null,
          stalled_since: null,
          author_replied_after_request_changes: false,
          exact_head_reviews: [],
        },
      },
    ],
  });

  // At 23h since stalled_since (2026-11-20T09:00 -> 2026-11-21T08:00), PR #201 is excluded even with newer updatedAt.
  const before24h = observeStewardSignalMonitor({
    signal_scope: stalledInternalScope,
    repository_snapshot: makeRepo("2026-11-21T08:30:00Z"),
    observed_at: "2026-11-21T08:00:00Z",
  });
  assert.deepEqual(before24h.scoped_repository_items, []);

  // At 24h exact (2026-11-21T09:00), PR #201 enters 24h_to_72h bucket.
  const at24h = observeStewardSignalMonitor({
    signal_scope: stalledInternalScope,
    previous_hash: before24h.result_hash,
    repository_snapshot: makeRepo("2026-11-21T08:30:00Z"),
    observed_at: "2026-11-21T09:00:00Z",
  });
  assert.equal(at24h.material_change, true);
  assert.equal(at24h.scoped_repository_items[0].number, 201);
  assert.equal(at24h.scoped_repository_items[0].stalled_threshold_met, true);
  assert.equal(at24h.scoped_repository_items[0].stalled_bucket, "24h_to_72h");

  // Bot comment bumping updatedAt at 71h produces zero hash change in the same bucket.
  const at71hBotBump = observeStewardSignalMonitor({
    signal_scope: stalledInternalScope,
    previous_hash: at24h.result_hash,
    repository_snapshot: makeRepo("2026-11-23T07:55:00Z"),
    observed_at: "2026-11-23T08:00:00Z",
  });
  assert.equal(at71hBotBump.result_hash, at24h.result_hash);
  assert.equal(at71hBotBump.material_change, false);

  // Crossing into 72h_plus and 7d_plus wakes the monitor exactly at bucket boundaries.
  const at72h = observeStewardSignalMonitor({
    signal_scope: stalledInternalScope,
    previous_hash: at71hBotBump.result_hash,
    repository_snapshot: makeRepo("2026-11-23T07:55:00Z"),
    observed_at: "2026-11-23T09:00:00Z",
  });
  assert.equal(at72h.material_change, true);
  assert.equal(at72h.scoped_repository_items[0].stalled_bucket, "72h_plus");

  const at73h = observeStewardSignalMonitor({
    signal_scope: stalledInternalScope,
    previous_hash: at72h.result_hash,
    repository_snapshot: makeRepo("2026-11-23T07:55:00Z"),
    observed_at: "2026-11-23T10:00:00Z",
  });
  assert.equal(at73h.material_change, false);

  const at7d = observeStewardSignalMonitor({
    signal_scope: stalledInternalScope,
    previous_hash: at73h.result_hash,
    repository_snapshot: makeRepo("2026-11-23T07:55:00Z"),
    observed_at: "2026-11-27T09:00:00Z",
  });
  assert.equal(at7d.material_change, true);
  assert.equal(at7d.scoped_repository_items[0].stalled_bucket, "7d_plus");

  // External monitor prioritizes awaiting_maintainer_reply immediately (<24h) and drops stale-commit RC (#203).
  const externalObs = observeStewardSignalMonitor({
    signal_scope: stalledExternalScope,
    repository_snapshot: makeRepo("2026-11-21T08:30:00Z"),
    observed_at: "2026-11-21T09:00:00Z",
  });
  assert.deepEqual(externalObs.scoped_repository_items.map((row: {number: number}) => row.number), [202]);
  assert.equal(externalObs.scoped_repository_items[0].stalled_threshold_met, false);
  assert.equal(externalObs.scoped_repository_items[0].stalled_bucket, "awaiting_maintainer_reply");

  // Scoped monitor WITHOUT min_stalled_hours retains updatedAt sensitivity even when items carry stalled_since.
  const noAgeScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "include",
      authors: ["alice"],
    },
  };
  const noAgeInitial = observeStewardSignalMonitor({
    signal_scope: noAgeScope,
    repository_snapshot: makeRepo("2026-11-21T08:30:00Z"),
    observed_at: "2026-11-21T09:00:00Z",
  });
  const noAgeUpdated = observeStewardSignalMonitor({
    signal_scope: noAgeScope,
    previous_hash: noAgeInitial.result_hash,
    repository_snapshot: makeRepo("2026-11-21T08:45:00Z"),
    observed_at: "2026-11-21T09:15:00Z",
  });
  assert.equal(noAgeUpdated.material_change, true);

  // Scoped monitor with sub-24h min_stalled_hours (e.g. 6h) emits under_24h at 7h and transitions to 24h_to_72h at 24h.
  const sixHourScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "include",
      authors: ["alice"],
      min_stalled_hours: 6,
    },
  };
  const at7h = observeStewardSignalMonitor({
    signal_scope: sixHourScope,
    repository_snapshot: makeRepo("2026-11-20T15:00:00Z"),
    observed_at: "2026-11-20T16:00:00Z",
  });
  assert.equal(at7h.scoped_repository_items.length, 1);
  assert.equal(at7h.scoped_repository_items[0].stalled_threshold_met, true);
  assert.equal(at7h.scoped_repository_items[0].stalled_bucket, "under_24h");
  const at24hFromSixHour = observeStewardSignalMonitor({
    signal_scope: sixHourScope,
    previous_hash: at7h.result_hash,
    repository_snapshot: makeRepo("2026-11-21T08:30:00Z"),
    observed_at: "2026-11-21T09:00:00Z",
  });
  assert.equal(at24hFromSixHour.material_change, true);
  assert.equal(at24hFromSixHour.scoped_repository_items[0].stalled_bucket, "24h_to_72h");
});

test("observeStewardSignalMonitor fails closed on comments_coverage_incomplete only for matching stalled-age PRs", () => {
  const externalStalledScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "exclude",
      authors: ["alice"],
      min_stalled_hours: 24,
    },
  };
  const internalStalledScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "include",
      authors: ["alice"],
      min_stalled_hours: 24,
    },
  };
  const noAgeScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "exclude",
      authors: ["alice"],
    },
  };

  const makeExternalPr = (overrides: Record<string, unknown> = {}) => ({
    number: 302,
    title: "External RC awaiting maintainer reply",
    isDraft: false,
    mergeable: "MERGEABLE",
    author: {login: "ext-user"},
    updatedAt: "2026-11-21T08:30:00Z",
    stalled_since: "2026-11-21T07:00:00Z",
    author_replied_after_request_changes: true,
    comments_coverage_incomplete: false,
    review_summary: {
      effective_verdict: "REQUEST_CHANGES",
      has_review_conflict: false,
      latest_request_changes_review_id: "PRR_302",
      stalled_since: "2026-11-21T07:00:00Z",
      author_replied_after_request_changes: true,
      comments_coverage_incomplete: false,
      exact_head_reviews: [],
    },
    ...overrides,
  });

  const baseline = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [makeExternalPr()]},
    observed_at: "2026-11-21T09:00:00Z",
  });
  assert.equal(baseline.observation_incomplete, false);
  assert.equal(baseline.scoped_repository_items[0].stalled_bucket, "awaiting_maintainer_reply");

  // Truncated comment suffix (<24h and >=24h) fails closed and preserves previous_hash,
  // whether comments_coverage_incomplete is surfaced at top-level or inside review_summary.
  for (const [observedAt, truncatedItem] of [
    [
      "2026-11-21T10:00:00Z",
      makeExternalPr({
        updatedAt: "2026-11-21T09:50:00Z",
        author_replied_after_request_changes: false,
        comments_coverage_incomplete: true,
        review_summary: {
          ...makeExternalPr().review_summary,
          author_replied_after_request_changes: false,
          comments_coverage_incomplete: true,
        },
      }),
    ],
    [
      "2026-11-22T09:00:00Z",
      makeExternalPr({
        updatedAt: "2026-11-22T08:30:00Z",
        author_replied_after_request_changes: false,
        comments_coverage_incomplete: false,
        review_summary: {
          ...makeExternalPr().review_summary,
          author_replied_after_request_changes: false,
          comments_coverage_incomplete: true,
        },
      }),
    ],
  ] as const) {
    const truncated = observeStewardSignalMonitor({
      signal_scope: externalStalledScope,
      previous_hash: baseline.result_hash,
      repository_snapshot: {status: "ok", repository: "acme/widgets", items: [truncatedItem]},
      observed_at: observedAt,
    });
    assert.deepEqual(truncated, {
      result_hash: baseline.result_hash,
      material_change: false,
      observation_incomplete: true,
      own_monitor_bookkeeping_excluded: true,
    });
  }

  // Recovered complete coverage with bumped updatedAt yields identical hash and material_change=false.
  const recovered = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    previous_hash: baseline.result_hash,
    repository_snapshot: {
      status: "ok",
      repository: "acme/widgets",
      items: [makeExternalPr({updatedAt: "2026-11-21T10:30:00Z"})],
    },
    observed_at: "2026-11-21T11:00:00Z",
  });
  assert.equal(recovered.result_hash, baseline.result_hash);
  assert.equal(recovered.material_change, false);
  assert.equal(recovered.observation_incomplete, false);

  // Maintainer reply with complete coverage exits awaiting_maintainer_reply.
  const maintainerCleared = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    previous_hash: baseline.result_hash,
    repository_snapshot: {
      status: "ok",
      repository: "acme/widgets",
      items: [
        makeExternalPr({
          author_replied_after_request_changes: false,
          review_summary: {
            ...makeExternalPr().review_summary,
            author_replied_after_request_changes: false,
          },
        }),
      ],
    },
    observed_at: "2026-11-21T11:00:00Z",
  });
  assert.equal(maintainerCleared.material_change, true);
  assert.equal(maintainerCleared.observation_incomplete, false);
  assert.deepEqual(maintainerCleared.scoped_repository_items, []);

  // Non-age-filtered scopes and non-matching items are unaffected by comments_coverage_incomplete.
  const truncatedExternal = makeExternalPr({
    author_replied_after_request_changes: false,
    comments_coverage_incomplete: true,
    review_summary: {
      ...makeExternalPr().review_summary,
      author_replied_after_request_changes: false,
      comments_coverage_incomplete: true,
    },
  });
  const repositoryItems = [
    {...truncatedExternal, number: 300, isDraft: true},
    {...truncatedExternal, number: 301, author: {login: " ALICE "}},
    truncatedExternal,
    {...truncatedExternal, number: 303, mergeable: "CONFLICTING"},
    makeExternalPr({number: 304}),
    {...truncatedExternal, number: 305, review_summary: {effective_verdict: "APPROVE"}},
    {...truncatedExternal, number: 306, comments_coverage_incomplete: false},
  ];
  const planParams = {repository_items: repositoryItems, observed_at: "2026-11-21T11:00:00Z"};
  assert.deepEqual(planStewardSignalCommentBackfill({...planParams, signal_scope: {
    ...externalStalledScope, pr_filter: {...externalStalledScope.pr_filter, exclude_conflicting: true},
  }}), {pr_numbers: [302, 306]});
  assert.deepEqual(planStewardSignalCommentBackfill({
    ...planParams, signal_scope: internalStalledScope,
  }), {pr_numbers: []});
  assert.deepEqual(planStewardSignalCommentBackfill({
    ...planParams, signal_scope: noAgeScope,
  }), {pr_numbers: []});
  const noAgeObs = observeStewardSignalMonitor({
    signal_scope: noAgeScope,
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [truncatedExternal]},
    observed_at: "2026-11-21T11:00:00Z",
  });
  assert.equal(noAgeObs.observation_incomplete, false);
  assert.equal(noAgeObs.scoped_repository_items.length, 1);

  const internalObs = observeStewardSignalMonitor({
    signal_scope: internalStalledScope,
    repository_snapshot: {
      status: "ok",
      repository: "acme/widgets",
      items: [
        truncatedExternal,
        makeExternalPr({number: 303, isDraft: true, comments_coverage_incomplete: true}),
      ],
    },
    observed_at: "2026-11-21T11:00:00Z",
  });
  assert.equal(internalObs.observation_incomplete, false);
  assert.deepEqual(internalObs.scoped_repository_items, []);

  const externalIgnoringDraftOrApprove = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    repository_snapshot: {
      status: "ok",
      repository: "acme/widgets",
      items: [
        makeExternalPr({number: 304, isDraft: true, comments_coverage_incomplete: true}),
        makeExternalPr({
          number: 305,
          comments_coverage_incomplete: true,
          review_summary: {
            effective_verdict: "APPROVE",
            has_review_conflict: false,
            comments_coverage_incomplete: true,
            exact_head_reviews: [],
          },
        }),
      ],
    },
    observed_at: "2026-11-21T11:00:00Z",
  });
  assert.equal(externalIgnoringDraftOrApprove.observation_incomplete, false);
  assert.deepEqual(externalIgnoringDraftOrApprove.scoped_repository_items, []);
});

test("Steward comment coverage respects the exact age boundary and reply bypass", () => {
  const observedAt = "2026-11-22T08:00:00.000Z";
  const scope = {schema_version: "steward_signal_monitor_scope_v0", include_portfolio: false,
    include_inbox: false, pr_filter: {min_stalled_hours: 24, author_mode: "include", authors: ["alice"]}};
  const younger = {number: 11, author: {login: "alice"}, stalled_since: "2026-11-21T08:00:00.001Z",
    comments_coverage_incomplete: true, review_summary: {effective_verdict: "REQUEST_CHANGES"}};
  const exact = {...younger, number: 12, stalled_since: "2026-11-21T08:00:00.000Z"};
  const invalid = {...younger, number: 13, stalled_since: "invalid"};
  const plan = (signalScope: typeof scope, items: unknown[]) => planStewardSignalCommentBackfill({
    signal_scope: signalScope, repository_items: items, observed_at: observedAt,
  });
  const observe = (signalScope: typeof scope, items: unknown[]) => observeStewardSignalMonitor({
    signal_scope: signalScope, repository_snapshot: {status: "ok", items}, observed_at: observedAt,
    previous_hash: "sha256:prior",
  });

  assert.deepEqual(plan(scope, [younger, exact, invalid]), {pr_numbers: [12, 13]});
  const observation = observe(scope, [younger, {...exact, comments_coverage_incomplete: false}]);
  assert.equal(observation.observation_incomplete, false);
  assert.deepEqual((observation.scoped_repository_items as {number: number}[]).map(row => row.number), [12]);
  for (const unresolved of [exact, invalid]) {
    const held = observe(scope, [unresolved]);
    assert.equal(held.observation_incomplete, true);
    assert.equal(held.result_hash, "sha256:prior");
  }

  const external = {...scope, pr_filter: {...scope.pr_filter, author_mode: "exclude", authors: ["other"]}};
  assert.deepEqual(plan(external, [younger]), {pr_numbers: [11]});
  assert.equal(observe(external, [younger]).observation_incomplete, true);
  const reply = observe(external, [{...younger, comments_coverage_incomplete: false,
    author_replied_after_request_changes: true}]);
  assert.equal(reply.observation_incomplete, false);
  assert.equal((reply.scoped_repository_items as {stalled_bucket: string}[])[0]?.stalled_bucket,
    "awaiting_maintainer_reply");
});

test("observeStewardSignalMonitor preserves pre-#9207 upgrade parity and excludes pagination metadata from hashes", () => {
  const noAgeScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "exclude",
      authors: ["alice"],
    },
  };
  const mergeableScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      exclude_conflicting: true,
      effective_verdicts: ["APPROVE"],
      required_approver_declarations: ["gpt"],
    },
  };
  const externalStalledScope = {
    schema_version: "steward_signal_monitor_scope_v0",
    include_portfolio: false,
    include_inbox: false,
    pr_filter: {
      exclude_drafts: true,
      effective_verdicts: ["REQUEST_CHANGES"],
      author_mode: "exclude",
      authors: ["alice"],
      min_stalled_hours: 24,
    },
  };

  const pre9207RcPr = {
    number: 410,
    title: "External PR with RC",
    url: "https://github.com/acme/widgets/pull/410",
    headRefOid: "head410",
    isDraft: false,
    mergeable: "MERGEABLE",
    author: {login: "ext-user"},
    updatedAt: "2026-11-21T08:30:00Z",
    checks_summary: {total: 1, pass_count: 1, fail_count: 0, pending_count: 0},
    bypassed_gates: [],
    review_summary: {
      effective_verdict: "REQUEST_CHANGES",
      has_review_conflict: false,
      stale_review_count: 0,
      exact_head_reviews: [],
    },
  };
  const upgradedCompleteRcPr = {
    ...pre9207RcPr,
    stalled_since: "2026-11-21T07:00:00Z",
    author_replied_after_request_changes: true,
    comments_coverage_incomplete: false,
    stalled_threshold_met: false,
    stalled_bucket: "awaiting_maintainer_reply",
    review_summary: {
      ...pre9207RcPr.review_summary,
      latest_request_changes_review_id: "PRR_410",
      stalled_since: "2026-11-21T07:00:00Z",
      author_replied_after_request_changes: true,
      comments_coverage_incomplete: false,
    },
  };
  const simulatedBackfillFailureRcPr = {
    ...upgradedCompleteRcPr,
    author_replied_after_request_changes: false,
    comments_coverage_incomplete: true,
    stalled_bucket: "under_24h",
    review_summary: {
      ...upgradedCompleteRcPr.review_summary,
      author_replied_after_request_changes: false,
      comments_coverage_incomplete: true,
    },
  };

  for (const scope of [null, noAgeScope]) {
    const legacy = observeStewardSignalMonitor({
      signal_scope: scope,
      portfolio_observation: {result_hash: "p1"},
      inbox_items: [{request_id: "req_1"}],
      repository_snapshot: {status: "ok", repository: "acme/widgets", items: [pre9207RcPr]},
      observed_at: "2026-11-21T09:00:00Z",
    });
    const upgraded = observeStewardSignalMonitor({
      signal_scope: scope,
      previous_hash: legacy.result_hash,
      portfolio_observation: {result_hash: "p1"},
      inbox_items: [{request_id: "req_1"}],
      repository_snapshot: {status: "ok", repository: "acme/widgets", items: [upgradedCompleteRcPr]},
      observed_at: "2026-11-21T09:05:00Z",
    });
    assert.equal(upgraded.result_hash, legacy.result_hash);
    assert.equal(upgraded.material_change, false);
    assert.equal(upgraded.observation_incomplete, false);

    // Simulated backfill failure -> recovery on monitors without an age filter yields no false wakeup.
    const failedBackfill = observeStewardSignalMonitor({
      signal_scope: scope,
      previous_hash: legacy.result_hash,
      portfolio_observation: {result_hash: "p1"},
      inbox_items: [{request_id: "req_1"}],
      repository_snapshot: {status: "ok", repository: "acme/widgets", items: [simulatedBackfillFailureRcPr]},
      observed_at: "2026-11-21T09:10:00Z",
    });
    assert.equal(failedBackfill.result_hash, legacy.result_hash);
    assert.equal(failedBackfill.material_change, false);
    assert.equal(failedBackfill.observation_incomplete, false);

    const recoveredBackfill = observeStewardSignalMonitor({
      signal_scope: scope,
      previous_hash: failedBackfill.result_hash,
      portfolio_observation: {result_hash: "p1"},
      inbox_items: [{request_id: "req_1"}],
      repository_snapshot: {status: "ok", repository: "acme/widgets", items: [upgradedCompleteRcPr]},
      observed_at: "2026-11-21T09:15:00Z",
    });
    assert.equal(recoveredBackfill.result_hash, legacy.result_hash);
    assert.equal(recoveredBackfill.material_change, false);
    assert.equal(recoveredBackfill.observation_incomplete, false);

    // Real updatedAt change still triggers material_change=true when !usesStalledAgeFilter.
    const bumpedUpdatedAt = observeStewardSignalMonitor({
      signal_scope: scope,
      previous_hash: legacy.result_hash,
      portfolio_observation: {result_hash: "p1"},
      inbox_items: [{request_id: "req_1"}],
      repository_snapshot: {
        status: "ok",
        repository: "acme/widgets",
        items: [{...upgradedCompleteRcPr, updatedAt: "2026-11-21T09:20:00Z"}],
      },
      observed_at: "2026-11-21T09:25:00Z",
    });
    assert.equal(bumpedUpdatedAt.material_change, true);
    assert.equal(bumpedUpdatedAt.observation_incomplete, false);
    assert.notEqual(bumpedUpdatedAt.result_hash, legacy.result_hash);
  }

  // Mergeable non-age scope also retains 100% pre-#9207 hash parity.
  const pre9207Approved = {
    number: 411,
    title: "Approved PR",
    isDraft: false,
    mergeable: "MERGEABLE",
    author: {login: "alice"},
    updatedAt: "2026-11-21T08:00:00Z",
    review_summary: {
      effective_verdict: "APPROVE",
      has_review_conflict: false,
      exact_head_reviews: [
        {author_login: "bob", reviewer_declaration: "GPT-5.4", verdict: "APPROVE", valid_verdict_review: true, state: "APPROVED"},
      ],
    },
  };
  const upgradedApproved = {
    ...pre9207Approved,
    stalled_since: null,
    author_replied_after_request_changes: false,
    comments_coverage_incomplete: false,
    review_summary: {
      ...pre9207Approved.review_summary,
      latest_request_changes_review_id: null,
      stalled_since: null,
      author_replied_after_request_changes: false,
      comments_coverage_incomplete: false,
    },
  };
  const legacyMergeable = observeStewardSignalMonitor({
    signal_scope: mergeableScope,
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [pre9207Approved]},
    observed_at: "2026-11-21T09:00:00Z",
  });
  const upgradedMergeable = observeStewardSignalMonitor({
    signal_scope: mergeableScope,
    previous_hash: legacyMergeable.result_hash,
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [upgradedApproved]},
    observed_at: "2026-11-21T09:05:00Z",
  });
  assert.equal(upgradedMergeable.result_hash, legacyMergeable.result_hash);
  assert.equal(upgradedMergeable.material_change, false);

  // Even under an age filter, comments_coverage_incomplete metadata never enters result_hash.
  const withoutPaginationField = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    repository_snapshot: {status: "ok", repository: "acme/widgets", items: [pre9207RcPr]},
    observed_at: "2026-11-22T10:00:00Z",
  });
  const withPaginationFieldFalse = observeStewardSignalMonitor({
    signal_scope: externalStalledScope,
    previous_hash: withoutPaginationField.result_hash,
    repository_snapshot: {
      status: "ok",
      repository: "acme/widgets",
      items: [
        {
          ...pre9207RcPr,
          comments_coverage_incomplete: false,
          review_summary: {
            ...pre9207RcPr.review_summary,
            comments_coverage_incomplete: false,
          },
        },
      ],
    },
    observed_at: "2026-11-22T10:05:00Z",
  });
  assert.equal(withPaginationFieldFalse.result_hash, withoutPaginationField.result_hash);
  assert.equal(withPaginationFieldFalse.material_change, false);
});
