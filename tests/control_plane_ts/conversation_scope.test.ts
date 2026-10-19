import assert from "node:assert/strict";
import test from "node:test";
import {resolveConversationScope} from "../../loopx/control_plane/collaboration/conversation_scope.ts";

test("owner steward is independent of the selected project; project scope is exact", () => {
  assert.deepEqual(resolveConversationScope({channel_id: "manager", goal_id: "alpha"}),
    {kind: "owner_portfolio", goal_ids: null, private_conversation: true});
  for (const goal of ["alpha", "_alpha", ".alpha", "-alpha"]) {
    assert.deepEqual(resolveConversationScope({channel_id: `goal.${goal}`, goal_id: goal}),
      {kind: "owner_goal", goal_ids: [goal], private_conversation: true});
  }
});

test("a project name, role label or external anchor cannot grant a wider scope", () => {
  for (const input of [
    {channel_id: "goal.beta", goal_id: "alpha"},
    {channel_id: "goal.alpha"},
    {channel_id: "goal.loopx-manager", goal_id: "loopx-manager"},
    {channel_id: "task.alpha", goal_id: "alpha", role: "manager"},
    {channel_id: "manager.external.", goal_id: "alpha"},
    {channel_id: "goal.a/../b", goal_id: "a/../b"},
    {channel_id: "goal..", goal_id: "."},
    {channel_id: "goal...", goal_id: ".."},
    {channel_id: "goal.alpha", goal_id: "alpha", origin: "lark"},
    {channel_id: "goal.alpha", goal_id: "alpha", origin: "unknown"},
  ]) {
    assert.deepEqual(resolveConversationScope(input),
      {kind: "unavailable", goal_ids: [], private_conversation: false});
  }
  assert.deepEqual(resolveConversationScope({channel_id: "manager.external.audience", goal_id: "alpha"}),
    {kind: "external_audience", goal_ids: [], private_conversation: false});
});

test("declared project identity cannot fall back to a legacy manager channel", () => {
  const project = {kind: "project_workspace", project_ref: "a".repeat(24),
    workspace_path: "/synthetic/project", audience: "local_owner", grant: "workspace_read"};
  const bound = {...project, audience: "bound_owner", binding_id: "b".repeat(24),
    source_ref: "c".repeat(24), provider_ref: "d".repeat(24), operator_ref: "e".repeat(24)};
  const group = {...bound, audience: "bound_group", filesystem_scope: "workspace_only",
    group_ref: "f".repeat(24), topic_ref: "0".repeat(24)};
  for (const context of [project, bound, group, {...project, grant: "invalid"}]) {
    for (const identity of [
      {channel_id: "manager", goal_id: null, origin: "web"},
      {channel_id: "manager", goal_id: "alpha", origin: "web"},
      {channel_id: "manager", goal_id: "loopx-manager", origin: "web"},
      {channel_id: "manager.external.audience", goal_id: null, origin: "lark"},
    ]) {
      assert.deepEqual(resolveConversationScope({...identity, project_context: context}),
        {kind: "unavailable", goal_ids: [], private_conversation: false});
    }
  }
  // A nullable transport projection alone does not change a legacy manager.
  for (const goal of [undefined, "alpha", "loopx-manager"]) {
    assert.deepEqual(resolveConversationScope({channel_id: "manager", goal_id: goal, project_context: null}),
      {kind: "owner_portfolio", goal_ids: null, private_conversation: true});
  }
  for (const channel of ["manager", "manager.external.audience"]) {
    assert.deepEqual(resolveConversationScope({channel_id: channel, goal_id: null}),
      {kind: "unavailable", goal_ids: [], private_conversation: false});
  }
});
