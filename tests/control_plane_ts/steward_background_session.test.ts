import assert from "node:assert/strict";
import test from "node:test";
import {
  STEWARD_BACKGROUND_CHANNEL_PREFIX,
  STEWARD_BACKGROUND_SESSION_PREFIX,
  admitStewardManagedTurn,
  bindStewardManagedActor,
  stewardBackgroundSessionId,
  stewardManagedBasis,
} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";
import {listLocalBoundSessions} from "../../loopx/control_plane/collaboration/conversation_binding.ts";
import {resolveConversationScope} from "../../loopx/control_plane/collaboration/conversation_scope.ts";

function buildFixture() {
  const binding = {
    schema_version: "loopx_chat_conversation_binding_v0",
    binding_id: "b".repeat(24),
    transport_ref: "steward-app",
    provider_ref: "c".repeat(24),
    operator_ref: "d".repeat(24),
    project_ref: "a".repeat(24),
    context_kind: "steward",
    grant: "portfolio_read",
    executor_endpoint_id: "codex",
    enabled: true,
    goal_scope: "all_registered",
    goal_ids: [],
  };
  const source = "e".repeat(24);
  const stewardContext = {
    kind: "bound_steward",
    audience: "bound_owner",
    grant: "portfolio_read",
    binding_id: binding.binding_id,
    provider_ref: binding.provider_ref,
    operator_ref: binding.operator_ref,
    project_ref: binding.project_ref,
    source_ref: source,
    workspace_path: "/runtime/chat/manager-workspaces/original",
    goal_ids: [],
  };
  const session = {
    session_id: "f".repeat(32),
    agent_id: "codex",
    executor_endpoint_id: "codex",
    goal_id: "loopx-manager",
    session_mode: "managed_runtime",
    status: "ready",
    active_turn_id: null,
    upstream_thread_id: "original-thread",
    channel_id: `manager.external.native.${binding.binding_id}.${source}`,
    steward_context: stewardContext,
  };
  const carrier = {
    session_id: stewardBackgroundSessionId(binding.binding_id),
    agent_id: "codex",
    executor_endpoint_id: "codex",
    goal_id: "loopx-manager",
    session_mode: "managed_runtime",
    status: "ready",
    active_turn_id: null,
    upstream_thread_id: `steward-watch-${binding.binding_id}`,
    channel_id: `${STEWARD_BACKGROUND_CHANNEL_PREFIX}${binding.binding_id}`,
    project_context: null,
    steward_context: null,
    steward_background_context: {...stewardContext},
  };
  const observation = {
    transport_ref: binding.transport_ref,
    provider_ref: binding.provider_ref,
    operator_ref: binding.operator_ref,
    verified: true,
  };
  const workspacePath = "/runtime/chat/manager-workspaces/original";
  const goal = {
    goal_id: `steward-agent-${binding.binding_id}`,
    goal_instance_id: "original-goal-lifetime",
    workspace_path: workspacePath,
    registered_agents: ["steward"],
    creation_operation_id: `steward-managed:${binding.binding_id}:${source}`,
  };
  const actor = bindStewardManagedActor({
    binding,
    session,
    observation,
    workspace_path: workspacePath,
    queued_turn_count: 0,
    goal,
  });
  const preparedBinding = {...binding, managed_actor: actor};
  const monitorTodo = {
    todo_id: "todo_watch",
    task_class: "continuous_monitor",
    target_key: "steward-portfolio-watch",
    watch_only: true,
    claimed_by: "steward",
    status: "open",
    next_due_at: "2026-11-23T12:00:00Z",
  };
  const monitorRequest = {
    operation: "native_turn",
    host: "codex-app-server",
    actor,
    work_kind: "monitor",
    turn_instance_id: "monitor.bg.1",
    todo_id: monitorTodo.todo_id,
    validation_command: null,
    monitor_deadline: monitorTodo.next_due_at,
    monitor_lease_version: 0,
  };
  return {
    binding: preparedBinding,
    source,
    stewardContext,
    session,
    carrier,
    observation,
    workspacePath,
    goal,
    actor,
    monitorTodo,
    monitorRequest,
  };
}

test("background carrier Session uses external_audience scope and stays invisible to bound human session discovery", () => {
  const f = buildFixture();
  assert.equal(f.carrier.session_id, `${STEWARD_BACKGROUND_SESSION_PREFIX}${f.binding.binding_id}`);
  assert.equal(f.carrier.channel_id, `${STEWARD_BACKGROUND_CHANNEL_PREFIX}${f.binding.binding_id}`);

  const scope = resolveConversationScope(f.carrier);
  assert.deepEqual(scope, {
    kind: "external_audience",
    goal_ids: [],
    private_conversation: false,
  });
  assert.equal(scope.bound_steward, undefined);

  const state = {
    schema_version: "loopx_chat_conversation_bindings_v0",
    revision: 1,
    bindings: [f.binding],
  };
  const listed = listLocalBoundSessions({
    current: state,
    binding_id: f.binding.binding_id,
    observation: f.observation,
    available_projects: [{
      kind: "project_workspace",
      project_ref: f.binding.project_ref,
      workspace_path: f.workspacePath,
      audience: "local_owner",
      grant: "workspace_read",
    }],
    available_goal_ids: [f.goal.goal_id],
    sessions: [f.session, f.carrier],
  });
  assert.deepEqual(listed.session_ids, [f.session.session_id]);
});

test("background monitor turns admit and execute while the foreground human Steward Session is busy and queued", () => {
  const f = buildFixture();
  const busyHumanSession = {
    ...f.session,
    status: "busy",
    active_turn_id: "human-turn-in-progress",
  };
  const baseParams = {
    binding: f.binding,
    session: busyHumanSession,
    carrier_session: f.carrier,
    carrier_queued_turn_count: 0,
    observation: f.observation,
    workspace_path: f.workspacePath,
    queued_turn_count: 3,
    goal: f.goal,
    goal_active: true,
    request: f.monitorRequest,
    monitor_todo: f.monitorTodo,
  };

  // Pre-acceptance admission succeeds despite busy human session + 3 queued human turns.
  const admitted = admitStewardManagedTurn(baseParams);
  assert.equal(admitted.observation_only, true);
  assert.deepEqual(admitted.actor, f.actor);

  // Without carrier_session, legacy admission still blocks on busy human session.
  assert.throws(() => admitStewardManagedTurn({
    ...baseParams,
    carrier_session: undefined,
    carrier_queued_turn_count: undefined,
  }));

  // Active execution on carrier validates turn against carrier, not human session.
  const runningCarrier = {...f.carrier, status: "busy", active_turn_id: "bg-turn-1"};
  const runningTurn = {
    session_id: runningCarrier.session_id,
    turn_id: "bg-turn-1",
    status: "running",
    origin: "native_loopx",
    loopx_execution: true,
    loopx_request: f.monitorRequest,
  };
  const activeAdmitted = admitStewardManagedTurn({
    ...baseParams,
    carrier_session: runningCarrier,
    turn: runningTurn,
    scope: {current: "scope-hash-1", without_actor: "scope-hash-0"},
  });
  assert.equal(activeAdmitted.observation_only, true);

  // Busy or queued carrier blocks a second concurrent monitor turn.
  assert.throws(() => admitStewardManagedTurn({
    ...baseParams,
    carrier_session: runningCarrier,
  }));
  assert.throws(() => admitStewardManagedTurn({
    ...baseParams,
    carrier_queued_turn_count: 1,
  }));

  // Turn bound to the wrong session or wrong active_turn_id is rejected.
  assert.throws(() => admitStewardManagedTurn({
    ...baseParams,
    carrier_session: runningCarrier,
    turn: {...runningTurn, session_id: f.session.session_id},
    scope: {current: "scope-hash-1", without_actor: "scope-hash-0"},
  }));
  assert.throws(() => admitStewardManagedTurn({
    ...baseParams,
    carrier_session: runningCarrier,
    turn: {...runningTurn, turn_id: "other-turn"},
    scope: {current: "scope-hash-1", without_actor: "scope-hash-0"},
  }));
});

test("background carrier rejects non-monitor work and any tampered carrier identity or authority", () => {
  const f = buildFixture();
  const baseParams = {
    binding: f.binding,
    session: f.session,
    carrier_session: f.carrier,
    carrier_queued_turn_count: 0,
    observation: f.observation,
    workspace_path: f.workspacePath,
    queued_turn_count: 0,
    goal: f.goal,
    goal_active: true,
    request: f.monitorRequest,
    monitor_todo: f.monitorTodo,
  };

  // Non-monitor work cannot use carrier_session to bypass foreground queue fencing.
  assert.throws(() => stewardManagedBasis({
    ...baseParams,
    request: {...f.monitorRequest, work_kind: "native_turn"},
  }));
  assert.throws(() => admitStewardManagedTurn({
    ...baseParams,
    monitor_todo: undefined,
    request: {
      operation: "native_turn",
      host: "codex-app-server",
      actor: f.actor,
      work_kind: "native_turn",
      turn_instance_id: "advancement.1",
      todo_id: "todo_adv",
      validation_command: null,
    },
  }));

  for (const invalidCarrier of [
    {...f.carrier, session_id: "0".repeat(32)},
    {...f.carrier, channel_id: f.session.channel_id},
    {...f.carrier, channel_id: "manager.external.steward.background." + "0".repeat(24)},
    {...f.carrier, goal_id: f.goal.goal_id},
    {...f.carrier, session_mode: "attached_host"},
    {...f.carrier, status: "closed"},
    {...f.carrier, project_context: {kind: "project_workspace"}},
    {...f.carrier, steward_context: f.stewardContext},
    {...f.carrier, managed_agent_id: "steward"},
    {...f.carrier, steward_background_context: null},
    {...f.carrier, steward_background_context: {...f.stewardContext, kind: "project_workspace"}},
    {...f.carrier, steward_background_context: {...f.stewardContext, audience: "bound_group"}},
    {...f.carrier, steward_background_context: {...f.stewardContext, grant: "workspace_write"}},
    {...f.carrier, steward_background_context: {...f.stewardContext, binding_id: "0".repeat(24)}},
    {...f.carrier, steward_background_context: {...f.stewardContext, source_ref: "0".repeat(24)}},
    {...f.carrier, steward_background_context: {...f.stewardContext, provider_ref: "0".repeat(24)}},
    {...f.carrier, steward_background_context: {...f.stewardContext, operator_ref: "0".repeat(24)}},
    {...f.carrier, steward_background_context: {...f.stewardContext, project_ref: "0".repeat(24)}},
  ]) {
    assert.throws(() => admitStewardManagedTurn({
      ...baseParams,
      carrier_session: invalidCarrier,
    }));
  }
});

test("monitor restart recovery on background carrier accepts both carrier and legacy pre-upgrade original turns", () => {
  const f = buildFixture();
  const busyHumanSession = {
    ...f.session,
    status: "busy",
    active_turn_id: "human-active-turn",
  };
  for (const originalSessionId of [f.carrier.session_id, f.session.session_id]) {
    const original = {
      turn_id: "1".repeat(32),
      session_id: originalSessionId,
      status: "failed",
      error_code: "server_restarted",
      error: "The original host stopped before observation.",
      origin: "native_loopx",
      loopx_execution: true,
      upstream_turn_id: null,
      response: null,
      delta_count: 0,
      loopx_request: f.monitorRequest,
    };
    const basis = {
      original,
      contexts: [],
      phases: [],
      event_kinds: ["turn.queued", "turn.failed"],
      poll_absent: true,
      old_worker_excluded: true,
      lease_available: true,
    };
    const recoveryParams = {
      binding: f.binding,
      session: busyHumanSession,
      carrier_session: f.carrier,
      carrier_queued_turn_count: 0,
      observation: f.observation,
      workspace_path: f.workspacePath,
      queued_turn_count: 2, // Foreground human queue is non-empty!
      goal: f.goal,
      goal_active: true,
      request: {
        ...f.monitorRequest,
        monitor_restart_recovery: {original_turn_id: original.turn_id},
      },
      monitor_todo: f.monitorTodo,
      monitor_restart_basis: basis,
    };
    assert.equal(admitStewardManagedTurn(recoveryParams).observation_only, true);

    // Non-empty carrier queue must still block recovery.
    assert.throws(() => admitStewardManagedTurn({
      ...recoveryParams,
      carrier_queued_turn_count: 1,
    }));
  }

  // Foreign session_id on original stopped turn is rejected.
  const foreignOriginal = {
    turn_id: "2".repeat(32),
    session_id: "9".repeat(32),
    status: "failed",
    error_code: "server_restarted",
    origin: "native_loopx",
    loopx_execution: true,
    upstream_turn_id: null,
    response: null,
    delta_count: 0,
    loopx_request: f.monitorRequest,
  };
  assert.throws(() => admitStewardManagedTurn({
    binding: f.binding,
    session: busyHumanSession,
    carrier_session: f.carrier,
    carrier_queued_turn_count: 0,
    observation: f.observation,
    workspace_path: f.workspacePath,
    queued_turn_count: 0,
    goal: f.goal,
    goal_active: true,
    request: {
      ...f.monitorRequest,
      monitor_restart_recovery: {original_turn_id: foreignOriginal.turn_id},
    },
    monitor_todo: f.monitorTodo,
    monitor_restart_basis: {
      original: foreignOriginal,
      contexts: [],
      phases: [],
      event_kinds: ["turn.queued", "turn.failed"],
      poll_absent: true,
      old_worker_excluded: true,
      lease_available: true,
    },
  }));
});
