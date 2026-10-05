import assert from "node:assert/strict";
import test from "node:test";
import {planConversationBinding, resolveBoundConversation, planBoundConversationRequest,
  stewardCommand, authorizeStewardCreation, resolveConversationAgentTarget} from "../../loopx/control_plane/collaboration/conversation_binding.ts";
import {resolveConversationScope, projectConversationIdentity} from "../../loopx/control_plane/collaboration/conversation_scope.ts";

const project = {kind: "project_workspace", project_ref: "a".repeat(24), workspace_path: "/authorized/notes",
  audience: "local_owner", grant: "workspace_read"};
const row = {schema_version: "loopx_chat_conversation_binding_v0", binding_id: "b".repeat(24),
  transport_ref: "notes-app", provider_ref: "c".repeat(24), operator_ref: "d".repeat(24),
  context_kind: "project", project_ref: project.project_ref, executor_endpoint_id: "codex",
  grant: "workspace_read", enabled: true};
const observation = {transport_ref: row.transport_ref, provider_ref: row.provider_ref,
  operator_ref: row.operator_ref, verified: true};
const current = {schema_version: "loopx_chat_conversation_bindings_v0", revision: 0, bindings: []};
const request = {current, expected_revision: 0, operation: "configure", binding: row, observation,
  available_projects: [project]};

test("explicit project writes remain App-bound and cannot exceed the host grant, another executor or an existing Session", () => {
  const original = planConversationBinding(request).state as typeof current;
  const use = {current: original, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const read = resolveBoundConversation(use);
  assert.equal(projectConversationIdentity({context: read.context}).sandbox, "read-only");
  assert.equal(projectConversationIdentity({context: {...project, grant: "workspace_write"}}).sandbox, "workspace-write");
  const writable = [{...project, grant: "workspace_write"}];
  assert.throws(() => planConversationBinding({...request, current: original, expected_revision: 1,
    binding: {...row, grant: "workspace_write"}, available_projects: writable}), /new binding identity/);
  const write = {...row, binding_id: "f".repeat(24), grant: "workspace_write"};
  const next = planConversationBinding({...request, current: original, expected_revision: 1, binding: write, available_projects: writable}).state;
  assert.throws(() => planConversationBinding({...request, binding: write}), /workspace/);
  assert.throws(() => resolveBoundConversation({...use, current: next, binding_id: write.binding_id}), /write grant/);
  use.available_projects = writable;
  const selected = resolveBoundConversation({...use, current: next, binding_id: write.binding_id});
  assert.equal(projectConversationIdentity({context: selected.context}).sandbox, "workspace-write");
  assert.deepEqual(resolveConversationScope({goal_id: null, channel_id: selected.channel_id,
    project_context: selected.context, origin: "lark"}), {kind: "project_workspace", goal_ids: [], private_conversation: false});
  assert.throws(() => resolveBoundConversation({...use, current: next}), /no longer authorized/);
  assert.throws(() => resolveBoundConversation({...use, current: next, binding_id: write.binding_id,
    session_context: read.context}), /context changed/);
  for (const bad of [{...write, executor_endpoint_id: "claude-code"},
    {...write, context_kind: "steward", goal_ids: []}, {...write, grant: "danger-full-access"}]) {
    assert.throws(() => planConversationBinding({...request, binding: bad}), /unsupported/);
  }
  assert.throws(() => resolveBoundConversation({...use, current: next, binding_id: write.binding_id,
    sender_ref: "c".repeat(24)}), /audience/);
});

test("binding independently verifies the owner and does not create a Goal", () => {
  const result = planConversationBinding(request);
  assert.equal(result.changed, true);
  const next = result.state as typeof current;
  assert.equal(next.revision, 1);
  assert.equal("goal_id" in next.bindings[0], false);
  const repeat = planConversationBinding({...request, current: next, expected_revision: 1,
    binding: {...row, binding_id: "e".repeat(24)}});
  assert.equal(repeat.changed, false);
  for (const proof of [{...observation, verified: false}, {...observation, operator_ref: "f".repeat(24)},
    {...observation, provider_ref: "e".repeat(24)}]) {
    assert.throws(() => planConversationBinding({...request, observation: proof}), /independently verified/);
  }
  assert.throws(() => planConversationBinding({...request, available_projects: []}), /workspace/);
  assert.throws(() => planConversationBinding({...request, expected_revision: 1}), /revision/);
});

test("a selected steward has a verified empty portfolio and adopts only its own exact new creation", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const next = planConversationBinding({...request, binding: steward}).state as typeof current;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.equal(resolveConversationScope({channel_id: selected.channel_id, goal_id: "loopx-manager",
    steward_context: selected.context, origin: "lark"}).bound_steward, true);
  assert.equal(resolveConversationScope({channel_id: selected.channel_id, goal_id: "loopx-manager",
    steward_context: selected.context, origin: "web"}).bound_steward, undefined);
  const context = selected.context as Record<string, unknown>;
  const proposal = {proposal_id: "proposal-" + "f".repeat(32), status: "applied", action_kind: "goal.create",
    context, receipt: {outcome: "goal_created", projection_verified: true, resource_ids: {goal_id: "fresh-goal"}}};
  const adoption = {current: next, expected_revision: 1, operation: "adopt_created_goal", binding_id: row.binding_id,
    context, proposal, goal: {goal_id: "fresh-goal", workspace_path: project.workspace_path, creation_operation_id: proposal.proposal_id}};
  const result = planConversationBinding(adoption).state as typeof current;
  assert.deepEqual((result.bindings[0] as Record<string, unknown>).goal_ids, ["fresh-goal"]);
  assert.deepEqual((resolveBoundConversation({...use, current: result, session_context: context}).context as Record<string, unknown>).goal_ids, ["fresh-goal"]);
  for (const bad of [{...adoption, goal: {...adoption.goal, workspace_path: "/other/private"}},
    {...adoption, proposal: {...proposal, status: "preview_ready"}},
    {...adoption, proposal: {...proposal, context: {...context, source_ref: "f".repeat(24)}}},
    {...adoption, proposal: {...proposal, receipt: {...proposal.receipt, projection_verified: false}}}]) {
    assert.throws(() => planConversationBinding(bad));
  }
});

test("a commission requires an explicit budget and confirmation stays within its audience and expiry", () => {
  assert.deepEqual(stewardCommand({command: "commission", message: "/delegate --tokens 12000 Read README"}),
    {argument: "Read README", native_token_budget: 12000});
  for (const message of ["delegate work", "/delegate Read README", "/delegate --tokens 0 work", "/delegate --tokens 1000"])
    assert.throws(() => stewardCommand({command: "commission", message}));
  const context = {...project, kind: "bound_steward", audience: "bound_owner", grant: "portfolio_read", goal_ids: [],
    binding_id: row.binding_id, source_ref: "e".repeat(24), provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const proposal = {action_kind: "goal.create", created_at: "2026-01-01T10:00:00Z",
    context: {...context, kind: "manager", goal_id: "loopx-manager", expires_at: "2026-01-01T10:15:00Z"}};
  assert.equal(authorizeStewardCreation({context, proposal, now: "2026-01-01T10:01:00Z"}).authorized, true);
  assert.throws(() => authorizeStewardCreation({context, proposal, now: "2026-01-01T10:16:00Z"}), /expired/);
  assert.throws(() => authorizeStewardCreation({context: {...context, provider_ref: "f".repeat(24)}, proposal,
    now: "2026-01-01T10:01:00Z"}), /audience/);
  assert.throws(() => planBoundConversationRequest({binding: row, current_session: null,
    request: {request_ref: "e".repeat(24), command: "commission"}}), /selected steward/);
});

test("one App has one binding owner, and a context change cannot move an existing Session", () => {
  const next = planConversationBinding(request).state as typeof current;
  const second = {...row, transport_ref: "other-app", binding_id: "e".repeat(24)};
  assert.throws(() => planConversationBinding({...request, current: next, expected_revision: 1,
    binding: second, observation: {...observation, transport_ref: "other-app"}}), /ownership/);
  const other = {...project, project_ref: "f".repeat(24), workspace_path: "/authorized/other"};
  assert.throws(() => planConversationBinding({...request, current: next, expected_revision: 1,
    binding: {...row, project_ref: other.project_ref}, available_projects: [other]}), /new binding identity/);
});

test("external project audience has its own Session identity without portfolio or peer scope", () => {
  const next = planConversationBinding(request).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.equal(selected.channel_id, `project.external.${row.binding_id}.${use.source_ref}`);
  assert.deepEqual(resolveConversationScope({channel_id: selected.channel_id, project_context: selected.context,
    goal_id: null, origin: "lark"}), {kind: "project_workspace", goal_ids: [], private_conversation: false});
  for (const bad of [{...use, sender_ref: "f".repeat(24)}, {...use, private_human_message: false},
    {...use, available_projects: []}, {...use, observation: {...observation, provider_ref: "f".repeat(24)}}]) {
    assert.throws(() => resolveBoundConversation(bad));
  }
  assert.throws(() => resolveBoundConversation({...use, source_ref: "f".repeat(24), session_context: selected.context}), /context changed/);
  const revoked = planConversationBinding({current: next, expected_revision: 1,
    operation: "disconnect", binding_id: row.binding_id}).state;
  assert.throws(() => resolveBoundConversation({...use, current: revoked}), /no longer authorized/);
});


test("native commands retain their recorded target across a new Session and redelivery", () => {
  const request = {request_ref: "e".repeat(24), command: "stop", target_recorded: true,
    session_id: "original", turn_id: "original-turn"};
  const current = {session_id: "replacement", active_turn_id: "different-turn"};
  assert.deepEqual(planBoundConversationRequest({request, current_session: current}), {
    operation: "stop", session_id: "original", turn_id: "original-turn", response_code: "stop_requested"});
  assert.deepEqual(planBoundConversationRequest({request: {...request, command: null}, current_session: current}), {
    operation: "admit_turn", client_turn_id: `external-${request.request_ref}`, session_id: "original", turn_id: null});
  assert.equal(planBoundConversationRequest({request: {...request, command: "unsupported"},
    current_session: null}).response_code, "unsupported_attachment");
  assert.throws(() => planBoundConversationRequest({request: {...request, command: "grant"}, current_session: current}),
    /unsupported/);
});

test("status and help project only the verified binding without opening a Session", () => {
  const context = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]}).context;
  const input = {binding: row, context, current_session: null, queued_count: 0, active_turn: null,
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "f".repeat(24), command: "status"}};
  const plan = planBoundConversationRequest(input);
  assert.equal(plan.operation, "reply");
  assert.equal(plan.session_id, null);
  assert.equal(plan.response_code, "no_session");
  assert.deepEqual(plan.status_snapshot, {schema_version: "loopx_chat_bound_status_v0",
    observed_at: input.observed_at, context_kind: "project", workspace_path: project.workspace_path,
    executor_endpoint_id: "codex", grant: "workspace_read", authorized_commission_count: 0,
    session_status: null, active_turn_status: null, active_turn_observation_available: true, queued_count: 0});
  assert.equal(planBoundConversationRequest({...input, request: {...input.request, command: "help"}}).response_code,
    "conversation_help");
  for (const bad of [{...input, binding: {...row, provider_ref: "a".repeat(24)}},
    {...input, queued_count: -1}, {...input, queued_count: .5}, {...input, queued_count: 1},
    {...input, observed_at: "unknown"}]) assert.throws(() => planBoundConversationRequest(bad));
});

test("status reads the canonical queue and exact active Turn, retaining unavailable evidence", () => {
  const selected = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]});
  const session = {session_id: "original", channel_id: selected.channel_id, goal_id: null,
    project_context: selected.context, status: "busy", active_turn_id: "original-turn"};
  const turn = {session_id: session.session_id, turn_id: session.active_turn_id, status: "running"};
  const input = {binding: row, context: selected.context, current_session: session, queued_count: 2,
    active_turn: turn, observed_at: "2026-01-01T10:00:00Z",
    request: {request_ref: "f".repeat(24), command: "status"}};
  const snapshot = planBoundConversationRequest(input).status_snapshot as Record<string, unknown>;
  assert.equal(snapshot.queued_count, 2);
  assert.equal(snapshot.active_turn_status, "running");
  assert.equal((planBoundConversationRequest({...input, active_turn: null}).status_snapshot as Record<string, unknown>)
    .active_turn_observation_available, false);
  for (const bad of [{...input, current_session: {...session, channel_id: "another-owner"}},
    {...input, active_turn: {...turn, session_id: "another-session"}},
    {...input, active_turn: {...turn, turn_id: "later-turn"}},
    {...input, active_turn: {...turn, status: {pretend: "completed"}}}]) {
    assert.throws(() => planBoundConversationRequest(bad));
  }
});

test("steward status counts its fresh grant, not a global portfolio or old Session scope", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: ["fresh-goal"]};
  const context = {...project, kind: "bound_steward", audience: "bound_owner", grant: "portfolio_read",
    goal_ids: ["fresh-goal"], binding_id: row.binding_id, source_ref: "e".repeat(24),
    provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const plan = planBoundConversationRequest({binding: steward, context, queued_count: 0, active_turn: null,
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "f".repeat(24), command: "help"},
    current_session: {session_id: "steward", channel_id: `manager.external.native.${row.binding_id}.${context.source_ref}`,
      goal_id: "loopx-manager", steward_context: {...context, goal_ids: []}, status: "ready", active_turn_id: null}});
  assert.equal((plan.status_snapshot as Record<string, unknown>).authorized_commission_count, 1);
  assert.equal(JSON.stringify(plan).includes("fresh-goal"), false);
});

test("an exact registered attached Session needs its own App grant and workspace", () => {
  const target = {target_ref: "f".repeat(24), host_ref: "a".repeat(24), session_id: "exact-session", goal_id: "sample-goal",
    goal_instance_id: "lifetime-a", agent_id: "notes-worker", executor_endpoint_id: "codex"};
  const observed = {session: {...target, session_mode: "attached_host", status: "ready"},
    goal: {goal_id: target.goal_id, workspace_path: project.workspace_path, registered_agents: [target.agent_id]},
    host_binding_verified: true, host_ref: target.host_ref, host_audience_binding_ids: []};
  const configured = planConversationBinding(request).state as typeof current;
  const grantInput = {current: configured, expected_revision: 1, operation: "grant_agent_target",
    binding_id: row.binding_id, target, target_observation: observed, observation, available_projects: [project]};
  const longGoal = "g".repeat(160);
  assert.equal(planConversationBinding({...grantInput, target: {...target, goal_id: longGoal},
    target_observation: {...observed, session: {...observed.session, goal_id: longGoal},
      goal: {...observed.goal, goal_id: longGoal}}}).changed, true);
  const granted = planConversationBinding(grantInput).state as typeof current;
  const selected = resolveBoundConversation({current: granted, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]});
  const input = {binding: (granted.bindings as unknown[])[0], context: selected.context, target, target_observation: observed};
  assert.deepEqual(resolveConversationAgentTarget(input).target, target);
  for (const bad of [{...input, target: {...target, agent_id: "codex"}},
    {...input, context: {...selected.context as object, provider_ref: "e".repeat(24)}},
    {...input, target_observation: {...observed, host_binding_verified: false}},
    {...input, target_observation: {...observed, session: {...observed.session, goal_instance_id: "recreated"}}},
    {...input, target_observation: {...observed, goal: {...observed.goal, registered_agents: []}}},
    {...input, target_observation: {...observed, goal: {...observed.goal, workspace_path: "/other"}}},
    {...input, target_observation: {...observed, session: {...observed.session, external_conversation_binding_id: "e".repeat(24)}}}]) {
    assert.throws(() => resolveConversationAgentTarget(bad));
  }
  const revoked = planConversationBinding({current: granted, expected_revision: 2, operation: "revoke_agent_target",
    binding_id: row.binding_id, target_ref: target.target_ref, observation}).state as typeof current;
  assert.throws(() => resolveConversationAgentTarget({...input, binding: (revoked.bindings as unknown[])[0]}), /grant/);
});

test("attached status names the registered recipient and stop cannot pretend host push support", () => {
  const target = {target_ref: "f".repeat(24), host_ref: "a".repeat(24), session_id: "original", goal_id: "sample-goal",
    goal_instance_id: null, agent_id: "notes-worker", executor_endpoint_id: "codex"};
  const context = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]}).context;
  const session = {...target, session_mode: "attached_host", status: "busy", active_turn_id: "original-turn"};
  const input = {binding: {...row, agent_targets: [target]}, context, current_session: session, agent_target: target,
    queued_count: 2, active_turn: {session_id: "original", turn_id: "original-turn", status: "running"},
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "e".repeat(24), command: "status"}};
  const snapshot = planBoundConversationRequest(input).status_snapshot as Record<string, unknown>;
  assert.equal(snapshot.recipient_agent_id, "notes-worker");
  assert.equal(snapshot.queued_count, 2);
  for (const command of ["stop", "new"]) assert.equal(planBoundConversationRequest({...input,
    request: {...input.request, command}}).response_code, "attached_control_unavailable");
});
