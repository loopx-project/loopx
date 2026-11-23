import assert from "node:assert/strict";
import test from "node:test";

import {
  listLocalBoundSessions,
  planBoundConversationRequest,
  resolveBoundConversation,
} from "../../loopx/control_plane/collaboration/conversation_binding.ts";
import {
  normalizeStewardContext,
  resolveConversationScope,
  stewardConversationIdentity,
} from "../../loopx/control_plane/collaboration/conversation_scope.ts";
import { revokeConversationSources } from "../../loopx/control_plane/collaboration/source_grants.ts";

const BINDING_ID = "e0e2226022a24adf9209cbe7";
const PROVIDER_REF = "111111111111111111111111";
const OPERATOR_REF = "222222222222222222222222";
const SOURCE_REF = "333333333333333333333333";
const PROJECT_REF = "444444444444444444444444";
const TOPIC_REF = "555555555555555555555555";
const REQUEST_REF = "666666666666666666666666";

function baseFixtures() {
  const stewardBinding = {
    schema_version: "loopx_chat_conversation_binding_v0",
    binding_id: BINDING_ID,
    transport_ref: "personal-steward",
    provider_ref: PROVIDER_REF,
    operator_ref: OPERATOR_REF,
    context_kind: "steward",
    project_ref: PROJECT_REF,
    executor_endpoint_id: "codex",
    grant: "portfolio_read",
    enabled: true,
    goal_ids: ["goal-alpha"],
    goal_scope: "all_registered",
  };
  const projectBinding = {
    schema_version: "loopx_chat_conversation_binding_v0",
    binding_id: "777777777777777777777777",
    transport_ref: "project-bot",
    provider_ref: "888888888888888888888888",
    operator_ref: OPERATOR_REF,
    context_kind: "project",
    project_ref: PROJECT_REF,
    executor_endpoint_id: "codex",
    grant: "workspace_read",
    enabled: true,
  };
  const current = {
    schema_version: "loopx_chat_conversation_bindings_v0",
    revision: 3,
    app_steward_binding_id: BINDING_ID,
    bindings: [stewardBinding, projectBinding],
  };
  const observation = {
    verified: true,
    transport_ref: "personal-steward",
    provider_ref: PROVIDER_REF,
    operator_ref: OPERATOR_REF,
  };
  const availableProjects = [
    {
      kind: "project_workspace",
      project_ref: PROJECT_REF,
      workspace_path: "/workspace/steward-home",
      audience: "local_owner",
      grant: "workspace_read",
    },
  ];
  return { stewardBinding, projectBinding, current, observation, availableProjects };
}

test("steward topic_ref derives child topic channel and preserves bound_steward scope", () => {
  const context = normalizeStewardContext({
    kind: "bound_steward",
    project_ref: PROJECT_REF,
    workspace_path: "/workspace/steward-home",
    audience: "bound_owner",
    grant: "portfolio_read",
    binding_id: BINDING_ID,
    source_ref: SOURCE_REF,
    provider_ref: PROVIDER_REF,
    operator_ref: OPERATOR_REF,
    topic_ref: TOPIC_REF,
    goal_ids: ["goal-alpha"],
  });
  assert.equal(context.topic_ref, TOPIC_REF);
  const identity = stewardConversationIdentity({ context });
  const expectedChannel = `manager.external.native.${BINDING_ID}.${SOURCE_REF}.topic.${TOPIC_REF}`;
  assert.equal(identity.channel_id, expectedChannel);

  const scope = resolveConversationScope({
    goal_id: "loopx-manager",
    channel_id: expectedChannel,
    steward_context: context,
    origin: "lark",
  });
  assert.equal(scope.kind, "external_audience");
  assert.equal(scope.bound_steward, true);
  assert.equal(scope.private_conversation, false);

  assert.throws(
    () =>
      normalizeStewardContext({
        ...context,
        topic_ref: "not-a-valid-24-hex-ref",
      }),
    /invalid steward topic identity/,
  );
});

test("resolveBoundConversation admits private steward topic lanes while rejecting private project topic_ref", () => {
  const { current, observation, availableProjects } = baseFixtures();
  const resolved = resolveBoundConversation({
    current,
    binding_id: BINDING_ID,
    observation,
    available_projects: availableProjects,
    available_goal_ids: ["goal-alpha", "goal-beta"],
    source_ref: SOURCE_REF,
    sender_ref: OPERATOR_REF,
    private_human_message: true,
    topic_ref: TOPIC_REF,
  });
  assert.equal(
    resolved.channel_id,
    `manager.external.native.${BINDING_ID}.${SOURCE_REF}.topic.${TOPIC_REF}`,
  );
  assert.equal((resolved.context as Record<string, unknown>).topic_ref, TOPIC_REF);
  assert.equal(resolved.owner_manager_audience, true);

  assert.throws(
    () =>
      resolveBoundConversation({
        current,
        binding_id: "777777777777777777777777",
        observation: {
          verified: true,
          transport_ref: "project-bot",
          provider_ref: "888888888888888888888888",
          operator_ref: OPERATOR_REF,
        },
        available_projects: availableProjects,
        source_ref: SOURCE_REF,
        sender_ref: OPERATOR_REF,
        private_human_message: true,
        topic_ref: TOPIC_REF,
      }),
    /message audience is outside the binding grant/,
  );
});

test("listLocalBoundSessions keeps app_steward_entry pinned to primary lobby session", () => {
  const { current, observation, availableProjects } = baseFixtures();
  const lobbyContext = {
    kind: "bound_steward",
    project_ref: PROJECT_REF,
    workspace_path: "/workspace/steward-home",
    audience: "bound_owner",
    grant: "portfolio_read",
    binding_id: BINDING_ID,
    source_ref: SOURCE_REF,
    provider_ref: PROVIDER_REF,
    operator_ref: OPERATOR_REF,
    goal_ids: ["goal-alpha"],
  };
  const topicContext = { ...lobbyContext, topic_ref: TOPIC_REF };
  const sessions = [
    {
      session_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      goal_id: "loopx-manager",
      agent_id: "codex",
      executor_endpoint_id: "codex",
      channel_id: `manager.external.native.${BINDING_ID}.${SOURCE_REF}`,
      session_mode: "managed_runtime",
      status: "ready",
      project_context: null,
      steward_context: lobbyContext,
    },
    {
      session_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      goal_id: "loopx-manager",
      agent_id: "codex",
      executor_endpoint_id: "codex",
      channel_id: `manager.external.native.${BINDING_ID}.${SOURCE_REF}.topic.${TOPIC_REF}`,
      session_mode: "managed_runtime",
      status: "ready",
      project_context: null,
      steward_context: topicContext,
    },
  ];

  const allLocal = listLocalBoundSessions({
    current,
    binding_id: BINDING_ID,
    observation,
    available_projects: availableProjects,
    available_goal_ids: ["goal-alpha"],
    sessions,
  });
  assert.deepEqual(allLocal.session_ids, [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  ]);

  const appEntry = listLocalBoundSessions({
    current,
    binding_id: BINDING_ID,
    observation,
    available_projects: availableProjects,
    available_goal_ids: ["goal-alpha"],
    sessions,
    app_steward_entry: true,
  });
  assert.deepEqual(appEntry.session_ids, ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]);

  const statusPlan = planBoundConversationRequest({
    request: {
      request_ref: REQUEST_REF,
      binding_id: BINDING_ID,
      source: {
        source_ref: SOURCE_REF,
        sender_ref: OPERATOR_REF,
        private_human_message: true,
        topic_ref: TOPIC_REF,
      },
      command: "status",
      message: "/status",
    },
    binding: current.bindings[0],
    current_session: sessions[1],
    context: topicContext,
    observed_at: "2026-11-23T11:00:00Z",
    queued_count: 0,
    active_turn: null,
  });
  assert.equal(statusPlan.operation, "reply");
  assert.equal(statusPlan.session_id, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");

  const revoked = revokeConversationSources({
    binding_id: BINDING_ID,
    policy: {
      schema_version: "loopx_manager_context_policy_v1",
      sources: {
        [`manager.external.native.${BINDING_ID}.${SOURCE_REF}`]: {
          sender_ids: [OPERATOR_REF],
          local_delivery_scope: "all_registered",
        },
        [`manager.external.native.${BINDING_ID}.${SOURCE_REF}.topic.${TOPIC_REF}`]: {
          sender_ids: [OPERATOR_REF],
          local_delivery_scope: "all_registered",
        },
      },
    },
  });
  assert.equal(revoked.would_change, true);
  const sources = (revoked.policy as Record<string, Record<string, Record<string, unknown>>>).sources;
  assert.deepEqual(sources[`manager.external.native.${BINDING_ID}.${SOURCE_REF}`].sender_ids, []);
  assert.deepEqual(
    sources[`manager.external.native.${BINDING_ID}.${SOURCE_REF}.topic.${TOPIC_REF}`].sender_ids,
    [],
  );
});
