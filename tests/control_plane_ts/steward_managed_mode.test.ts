import assert from "node:assert/strict";
import test from "node:test";
import {bindStewardManagedActor, planStewardManagedMode} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";
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
