import assert from "node:assert/strict";
import test from "node:test";
import {planConversationBinding, resolveBoundConversation, planBoundConversationRequest,
  stewardCommand, authorizeStewardCreation, resolveConversationAgentTarget,
  listLocalBoundSessions} from "../../loopx/control_plane/collaboration/conversation_binding.ts";
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

for (const kind of ["project", "steward"]) {
  test(`${kind} routing recovery is bounded, source-bound and independent of context migrations`, () => {
    const selected = kind === "steward" ? {...row, context_kind: kind, grant: "portfolio_read", goal_ids: []} : row;
    const configured = planConversationBinding({...request, binding: selected}).state;
    const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
    const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
      observation, available_projects: [project], ...source}).context;
    const session = {session_id: "original", goal_id: kind === "steward" ? "loopx-manager" : null,
      agent_id: "codex", upstream_mode: "chat", status: "ready",
      upstream_thread_id: "native", active_turn_id: null, manager_context_version: 21,
      channel_id: `${kind === "steward" ? "manager.external.native" : "project.external"}.${row.binding_id}.${source.source_ref}`,
      ...(kind === "steward" ? {steward_context: context} : {project_context: context})};
    const failed = {session_id: "original", turn_id: "failed", status: "failed", delta_count: 0,
      response: null, error_code: "host_gate", upstream_turn_id: "native-failed", completed_at: "2026-01-01T00:00:00Z"};
    const observed = {thread_id: "native", turn_id: "native-failed", codex_error_info: "other",
      pre_execution_routing_timeout: true, only_pre_execution_items: true, only_original_user_item: false};
    const packet = {request: {request_ref: "f".repeat(24), command: null, status: "accepted", delivery_verified: true,
      binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
      binding: selected, current_session: session, context_recovery: {version: 21, failed_turn: failed,
        observed_at: "2026-01-01T00:10:00Z", steering_observed: false, source_verified: true, native_observation: observed}};
    const plan = planBoundConversationRequest(packet);
    assert.equal(plan.recovery_eligible, true);
    assert.equal(plan.routing_attempt, 1);
    assert.equal(plan.not_before, "2026-01-01T00:01:00.000Z");
    const reservation = {version: 21, failed_turn_id: "failed", routing_attempt: 1, client_turn_id: plan.client_turn_id};
    assert.deepEqual(planBoundConversationRequest({...packet, request: {...packet.request, context_recovery: reservation}}), plan);
    const next = {...packet, request: {...packet.request, turn_id: "next", context_recovery: reservation},
      context_recovery: {...packet.context_recovery, failed_turn: {...failed, turn_id: "next"}}};
    assert.equal(planBoundConversationRequest(next).routing_attempt, 2);
    assert.equal(planBoundConversationRequest(next).not_before, "2026-01-01T00:02:00.000Z");
    assert.equal(planBoundConversationRequest({...next, request: {...next.request,
      context_recovery: {...reservation, routing_attempt: 3}}}).recovery_candidate, false);
    for (const change of [{source_verified: false}, {steering_observed: true},
      {observed_at: "2026-01-01T00:00:59Z"}, {observed_at: "invalid"},
      {native_observation: {...observed, only_pre_execution_items: false}},
      {native_observation: {...observed, thread_id: "foreign"}},
      {native_observation: {...observed, codex_error_info: "cyberPolicy"}}]) {
      assert.notEqual(planBoundConversationRequest({...packet,
        context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
    }
    for (const change of [{status: "closed"}, {status: "stale"}, {goal_id: "other"}, {active_turn_id: "other"},
      {session_mode: "attached_host"}, {channel_id: "foreign"}]) {
      assert.notEqual(planBoundConversationRequest({...packet,
        current_session: {...session, ...change}}).recovery_eligible, true);
    }
  });
}

for (const kind of ["project", "steward"]) {
  const selected = kind === "steward" ? {...row, context_kind: kind, grant: "portfolio_read", goal_ids: []} : row;
  const configured = planConversationBinding({...request, binding: selected}).state;
  const lookup = {current: configured, binding_id: row.binding_id, observation, available_projects: [project]};
  const context = resolveBoundConversation({...lookup, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true}).context;
  const session = {session_id: "original", goal_id: kind === "steward" ? "loopx-manager" : null,
    agent_id: "codex", executor_endpoint_id: "codex", session_mode: "managed_runtime", status: "ready",
    channel_id: `${kind === "steward" ? "manager.external.native" : "project.external"}.${row.binding_id}.${"e".repeat(24)}`,
    ...(kind === "steward" ? {steward_context: context} : {project_context: context})};
  const continuation = {...lookup, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: false, origin: "web", session_context: context};

  test(`${kind} discovery reuses verified saved identity and excludes unrelated or closed Sessions`, () => {
    const snapshots = [session, {...session, session_id: "historical", status: "closed"},
      {session_id: "local", goal_id: null, channel_id: `project.${project.project_ref}`}];
    const before = structuredClone(snapshots);
    assert.deepEqual(listLocalBoundSessions({...lookup, sessions: snapshots}), {session_ids: ["original"]});
    assert.deepEqual(snapshots, before);
    assert.deepEqual(listLocalBoundSessions({...lookup, sessions: []}), {session_ids: []});
    assert.throws(() => listLocalBoundSessions({...lookup, sessions: [session, session]}), /ambiguous/);
    assert.deepEqual(resolveBoundConversation({...continuation, session}).context, context);
    assert.throws(() => resolveBoundConversation({...continuation, session: {...session, status: "closed"}}),
      /canonical bound Session identity/);
  });

  test(`${kind} discovery refuses changed canonical role, executor, channel or saved source`, () => {
    for (const change of [{goal_id: "another-goal"}, {channel_id: "manager"}, {agent_id: "other"},
      {executor_endpoint_id: "other"}, {session_mode: "attached_host"},
      {[kind === "steward" ? "project_context" : "steward_context"]: context},
      {[kind === "steward" ? "steward_context" : "project_context"]:
        {...context as Record<string, unknown>, operator_ref: "f".repeat(24)}},
      {[kind === "steward" ? "steward_context" : "project_context"]:
        {...context as Record<string, unknown>, source_ref: "f".repeat(24)}}]) {
      assert.throws(() => listLocalBoundSessions({...lookup, sessions: [{...session, ...change}]}));
      assert.throws(() => resolveBoundConversation({...continuation, session: {...session, ...change}}));
    }
    for (const change of [{current}, {available_projects: []},
      {observation: {...observation, verified: false}},
      {observation: {...observation, provider_ref: "f".repeat(24)}},
      {observation: {...observation, operator_ref: "f".repeat(24)}},
      {observation: {...observation, transport_ref: "another-app"}}]) {
      // An empty inventory is not permission to bypass current source checks.
      assert.throws(() => listLocalBoundSessions({...lookup, ...change, sessions: []}));
    }
  });
}

test("local discovery never promotes a group binding to a private recipient", () => {
  const group = "1".repeat(24);
  const configured = planConversationBinding({...request,
    binding: {...row, audience: "group", group_refs: [group]}, available_group_refs: [group]}).state;
  assert.throws(() => listLocalBoundSessions({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], sessions: []}), /private binding/);
});

test("an unfrozen recovered stop cannot borrow current cancellation scope", () => {
  const request = {request_ref: "e".repeat(24), command: "stop"};
  const current = {session_id: "new-session", active_turn_id: "new-active"};
  assert.throws(() => planBoundConversationRequest({request, current_session: current,
    recovering_request: true, background_turn_ids: ["new-background"]}), /original stop scope was not recorded/);
  assert.equal(planBoundConversationRequest({request, current_session: current,
    recovering_request: false}).turn_id, "new-active");
  assert.deepEqual(planBoundConversationRequest({request: {...request, target_recorded: true,
    session_id: "original", turn_id: null, background_turn_ids: ["old"]},
    current_session: current, recovering_request: true, background_turn_ids: ["new-background"]}), {
    operation: "stop", session_id: "original", turn_id: null,
    background_turn_ids: ["old"], response_code: "stop_requested"});
});

test("group topics require an observed App membership and cannot inherit private recipients or portfolio", () => {
  const group = "1".repeat(24), topic = "2".repeat(24);
  const configure = {...request, binding: {...row, audience: "group", group_refs: [group]},
    available_group_refs: [group]};
  const next = planConversationBinding(configure).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: topic, topic_ref: topic,
    group_ref: group, sender_ref: "3".repeat(24), private_human_message: false,
    group_human_message: true, observation, available_projects: [project]};
  const resolved = resolveBoundConversation(use);
  const context = resolved.context as Record<string, unknown>;
  assert.equal(context.audience, "bound_group");
  assert.equal(context.filesystem_scope, "workspace_only");
  assert.equal(resolved.owner_manager_audience, false);
  assert.deepEqual(resolveBoundConversation({...use, session_context: context}).context, context);
  assert.deepEqual(resolveConversationScope({goal_id: null, channel_id: resolved.channel_id,
    project_context: context, origin: "lark"}),
    {kind: "project_workspace", goal_ids: [], private_conversation: false});
  const host = projectConversationIdentity({context}).host_config as Record<string, unknown>;
  assert.deepEqual(host.skills, {include_instructions: false});
  assert.equal(host.project_doc_max_bytes, 0);
  assert.equal(host.allow_login_shell, false);
  for (const denied of [{group_ref: "4".repeat(24)}, {topic_ref: "4".repeat(24)},
    {private_human_message: true}, {group_human_message: false},
    {observation: {...observation, verified: false}}, {current}]) {
    assert.throws(() => resolveBoundConversation({...use, ...denied}));
  }
  for (const denied of [{available_group_refs: []}, {available_group_refs: undefined},
    {binding: {...configure.binding, context_kind: "steward", grant: "portfolio_read", goal_ids: []}},
    {binding: {...configure.binding, executor_endpoint_id: "claude-code"}},
    {binding: {...configure.binding, group_refs: [group, group]}}]) {
    assert.throws(() => planConversationBinding({...configure, ...denied}));
  }
  for (const command of ["agents", "select_agent", "select_project"]) {
    assert.equal(planBoundConversationRequest({binding: configure.binding,
      request: {request_ref: topic, command}, current_session: null}).response_code, "group_recipient_unavailable");
  }
  const changed = planConversationBinding({...configure, current: next, expected_revision: 1,
    binding: {...configure.binding, binding_id: "5".repeat(24), group_refs: [group, "4".repeat(24)]},
    available_group_refs: [group, "4".repeat(24)]}).state;
  assert.throws(() => resolveBoundConversation({...use, current: changed}), /no longer authorized/);
  assert.equal(project.audience, "local_owner");
});

test("only a current verified private steward binding proves the machine owner audience", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const next = planConversationBinding({...request, binding: steward}).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  assert.equal(resolveBoundConversation(use).owner_manager_audience, true);
  assert.equal(resolveBoundConversation({...use,
    available_projects: [{...project, filesystem_scope: "workspace_only"}]}).owner_manager_audience, false);
  const projectState = planConversationBinding(request).state;
  assert.equal(resolveBoundConversation({...use, current: projectState}).owner_manager_audience, false);
  for (const denied of [{private_human_message: false}, {sender_ref: "f".repeat(24)},
    {observation: {...observation, provider_ref: "f".repeat(24)}},
    {observation: {...observation, verified: false}}, {available_projects: []}, {current}]) {
    assert.throws(() => resolveBoundConversation({...use, ...denied}));
  }
});

test("images use the managed conversation and never disappear into commands or an attached host", () => {
  const request = {request_ref: "e".repeat(24), command: null};
  assert.equal(planBoundConversationRequest({request, current_session: null, attachment_count: 1}).operation, "admit_turn");
  for (const command of ["stop", "new", "select_agent", "commission"]) {
    assert.equal(planBoundConversationRequest({request: {...request, command}, current_session: null,
      attachment_count: 1}).response_code, "unsupported_attachment");
  }
  assert.equal(planBoundConversationRequest({request, current_session: null, attachment_count: 1,
    agent_target: {session_id: "existing-host"}}).response_code, "unsupported_attachment");
  for (const count of [-1, 5, 0.5, "1"]) {
    assert.throws(() => planBoundConversationRequest({request, current_session: null, attachment_count: count}), /attachment count/);
  }
});

test("owner steward scope reads the current registry, preserves its Session and never widens a project or legacy scope", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], goal_scope: "all_registered"};
  const next = planConversationBinding({...request, binding: steward}).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project], available_goal_ids: ["notes", "maintenance"]};
  const selected = resolveBoundConversation(use);
  assert.deepEqual((selected.context as Record<string, unknown>).goal_ids, ["maintenance", "notes"]);
  assert.deepEqual((resolveBoundConversation({...use, available_goal_ids: ["new-goal"], session_context: selected.context}).context as Record<string, unknown>).goal_ids, ["new-goal"]);
  assert.throws(() => resolveBoundConversation({...use, available_goal_ids: undefined}), /Goal scope/);
  assert.throws(() => resolveBoundConversation({...use, observation: {...observation, verified: false}}), /verified/);
  assert.throws(() => planConversationBinding({...request, binding: {...row, goal_scope: "all_registered"}}), /scope/);
  const restricted = planConversationBinding({...request, current: next, expected_revision: 1,
    binding: {...steward, goal_scope: "selected"}}).state;
  assert.deepEqual((resolveBoundConversation({...use, current: restricted}).context as Record<string, unknown>).goal_ids, []);
  assert.throws(() => resolveBoundConversation({...use, current: restricted, session_context: selected.context}), /context changed/);
  const legacy = planConversationBinding({...request, binding: {...steward, goal_scope: undefined}}).state;
  assert.deepEqual((resolveBoundConversation({...use, current: legacy}).context as Record<string, unknown>).goal_ids, []);
});

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

test("a selected steward retains its bounded portfolio for provider and local continuation", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const next = planConversationBinding({...request, binding: steward}).state as typeof current;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.equal(resolveConversationScope({channel_id: selected.channel_id, goal_id: "loopx-manager",
    steward_context: selected.context, origin: "lark"}).bound_steward, true);
  assert.equal(resolveConversationScope({channel_id: selected.channel_id, goal_id: "loopx-manager",
    steward_context: selected.context, origin: "web"}).bound_steward, true);
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

test("Todo confirmation names a current Goal, preserves its owner and cannot start work", () => {
  const context = {...project, kind: "bound_steward", audience: "bound_owner", grant: "portfolio_read",
    goal_ids: ["maintenance"], binding_id: row.binding_id, source_ref: "e".repeat(24),
    provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const proposal = {action_kind: "todo.create", created_at: "2026-01-01T10:00:00Z",
    context: {...context, kind: "manager", goal_id: "loopx-manager", expires_at: "2026-01-01T10:15:00Z"},
    normalized_parameters: {goal_id: "maintenance", text: "Remember a feature", priority: "P2"}};
  const request = {context, proposal, now: "2026-01-01T10:01:00Z", operation: "confirm"};
  assert.equal(authorizeStewardCreation(request).authorized, true);
  for (const change of [{goal_id: "foreign"}, {goal_id: undefined}, {start_execution: true}, {agent_id: "worker"}]) {
    assert.throws(() => authorizeStewardCreation({...request,
      proposal: {...proposal, normalized_parameters: {...proposal.normalized_parameters, ...change}}}), /Todo confirmation/);
  }
  for (const field of ["binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref"]) {
    assert.throws(() => authorizeStewardCreation({...request, context: {...context, [field]: "f".repeat(24)}}), /audience/);
  }
  for (const operation of ["stop", "resume"]) {
    assert.throws(() => authorizeStewardCreation({...request, operation}), /cannot start/);
  }
  assert.throws(() => authorizeStewardCreation({...request, now: "2026-01-01T10:16:00Z"}), /expired/);
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
  const cadence = {goal_id: target.goal_id, agent_id: target.agent_id, automation_id: null,
    configuration_revision: 4, min_interval_minutes: 60,
    eligibility: {state: "waiting", reason: "minimum_interval_wait", eligible_now: false,
      next_eligible_at_ms: 1791637200000}};
  const context = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]}).context;
  const session = {...target, session_mode: "attached_host", status: "busy", active_turn_id: "original-turn"};
  const input = {binding: {...row, agent_targets: [target]}, context, current_session: session, agent_target: target,
    queued_count: 2, active_turn: {session_id: "original", turn_id: "original-turn", status: "running"},
    automation_cadence: cadence,
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "e".repeat(24), command: "status"}};
  const snapshot = planBoundConversationRequest(input).status_snapshot as Record<string, unknown>;
  assert.equal(snapshot.recipient_agent_id, "notes-worker");
  assert.equal(snapshot.queued_count, 2);
  assert.deepEqual(snapshot.automation_cadence, cadence);
  for (const command of ["stop", "new"]) assert.equal(planBoundConversationRequest({...input,
    request: {...input.request, command}}).response_code, "attached_control_unavailable");
  assert.throws(() => planBoundConversationRequest({...input, automation_cadence: undefined}), /exact selected Agent status/);
  for (const automation_cadence of [
    {...cadence, goal_id: "another-goal"},
    {...cadence, agent_id: "another-agent"},
    {...cadence, automation_id: "timer"},
    {...cadence, eligibility: {...cadence.eligibility, next_eligible_at_ms: null}},
    {...cadence, eligibility: {state: "unavailable", reason: "owner_read_failed",
      eligible_now: null, next_eligible_at_ms: null}},
  ]) {
    assert.throws(() => planBoundConversationRequest({...input, automation_cadence}));
  }
  const unavailable = {...cadence, configuration_revision: null, min_interval_minutes: null,
    eligibility: {state: "unavailable", reason: "owner_read_failed", eligible_now: null, next_eligible_at_ms: null}};
  const unavailableSnapshot = planBoundConversationRequest({...input, automation_cadence: unavailable})
    .status_snapshot as Record<string, unknown>;
  assert.deepEqual(unavailableSnapshot.automation_cadence, unavailable);
  assert.throws(() => planBoundConversationRequest({...input, request: {...input.request, command: "help"}}),
    /exact selected Agent status/);
});


test("provider and authorized local input classify the same bounded private audience", () => {
  for (const contextKind of ["project", "steward"]) {
    const selectedRow = contextKind === "project" ? row : {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
    const next = planConversationBinding({...request, binding: selectedRow}).state;
    const selected = resolveBoundConversation({current: next, binding_id: row.binding_id,
      source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true,
      observation, available_projects: [project]});
    const session = {goal_id: contextKind === "project" ? null : "loopx-manager", channel_id: selected.channel_id,
      ...(contextKind === "project" ? {project_context: selected.context} : {steward_context: selected.context})};
    assert.deepEqual(resolveConversationScope({...session, origin: "external"}),
      resolveConversationScope({...session, origin: "lark"}));
    assert.deepEqual(resolveConversationScope({...session, origin: "web"}),
      resolveConversationScope({...session, origin: "external"}));
  }
});


test("local input requires the exact existing private context and fresh binding proof", () => {
  for (const kind of ["project", "steward"]) {
    const binding = kind === "project" ? row : {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
    const next = planConversationBinding({...request, binding}).state;
    const providerUse = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
      sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
    const original = resolveBoundConversation(providerUse);
    const local = {...providerUse, origin: "web", private_human_message: false, session_context: original.context};
    assert.deepEqual(resolveBoundConversation(local), original);
    const scope = resolveConversationScope({goal_id: kind === "project" ? null : "loopx-manager",
      channel_id: original.channel_id, project_context: original.context, steward_context: original.context, origin: "web"});
    assert.equal(scope.private_conversation, false);
    assert.equal(scope.kind, kind === "project" ? "project_workspace" : "external_audience");
    for (const bad of [{session_context: undefined}, {private_human_message: true}, {origin: "model"},
      {sender_ref: "f".repeat(24)}, {current}, {available_projects: []},
      {observation: {...observation, verified: false}},
      {session_context: {...original.context as object, source_ref: "f".repeat(24)}}]) {
      assert.throws(() => resolveBoundConversation({...local, ...bad}));
    }
  }
});

test("local input cannot impersonate a group participant or inherit its workspace", () => {
  const groupRef = "1".repeat(24), topic = "2".repeat(24);
  const group = {...row, audience: "group", group_refs: [groupRef]};
  const next = planConversationBinding({...request, binding: group, available_group_refs: [groupRef]}).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: topic, group_ref: groupRef, topic_ref: topic,
    sender_ref: "3".repeat(24), private_human_message: false, group_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.throws(() => resolveBoundConversation({...use, origin: "web", session_context: selected.context}));
  assert.equal(resolveConversationScope({goal_id: null, channel_id: selected.channel_id,
    project_context: selected.context, origin: "web"}).kind, "unavailable");
});
