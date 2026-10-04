import assert from "node:assert/strict";
import test from "node:test";
import {planConversationBinding, resolveBoundConversation, planBoundConversationRequest,
  stewardCommand, authorizeStewardCreation} from "../../loopx/control_plane/collaboration/conversation_binding.ts";
import {resolveConversationScope} from "../../loopx/control_plane/collaboration/conversation_scope.ts";

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
