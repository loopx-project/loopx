import assert from "node:assert/strict";
import test from "node:test";
import {resolveProjectConversation} from "../../loopx/control_plane/collaboration/project_conversation.ts";
import {resolveConversationScope} from "../../loopx/control_plane/collaboration/conversation_scope.ts";
import {requirePeerContextAccess} from "../../loopx/control_plane/collaboration/peer_context.ts";

const context = {kind: "project_workspace", project_ref: "a".repeat(24),
  workspace_path: "/fixture/notes", audience: "local_owner", grant: "workspace_read"};

test("ordinary project identity neither enrolls a Goal nor authorizes portfolio/peer reads", () => {
  const selected = resolveProjectConversation({project_ref: context.project_ref, available: [context]});
  const session = {goal_id: null, channel_id: selected.channel_id, project_context: selected.context};
  assert.deepEqual(resolveConversationScope(session), {
    kind: "project_workspace", goal_ids: [], private_conversation: true,
  });
  assert.throws(() => requirePeerContextAccess({conversation: session, goal_id: "unrelated", agent_ids: ["reviewer"]}));
  for (const forged of [
    {...session, goal_id: "unrelated"}, {...session, channel_id: "project.other"},
    {...session, origin: "lark"}, {...session, project_context: {...context, grant: "write"}},
  ]) assert.equal(resolveConversationScope(forged).kind, "unavailable");
});

test("a reference or path never creates a host grant; removed or changed grants fail closed", () => {
  assert.throws(() => resolveProjectConversation({project_ref: context.project_ref, available: []}));
  assert.throws(() => resolveProjectConversation({project_ref: context.project_ref, available: [context, context]}));
  assert.throws(() => resolveProjectConversation({project_ref: context.project_ref, available: [context],
    session_context: {...context, workspace_path: "/fixture/private"}}));
  assert.throws(() => resolveProjectConversation({project_ref: context.project_ref,
    available: [{...context, workspace_path: "../private"}]}));
});
