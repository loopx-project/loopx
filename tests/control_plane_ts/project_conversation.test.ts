import assert from "node:assert/strict";
import test from "node:test";
import {resolveProjectConversation} from "../../loopx/control_plane/collaboration/project_conversation.ts";
import {resolveConversationScope, projectConversationIdentity} from "../../loopx/control_plane/collaboration/conversation_scope.ts";
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

test("workspace-only filesystem scope is host-owned, persisted and narrower than either grant", () => {
  for (const grant of ["workspace_read", "workspace_write"]) {
    const scoped = {...context, grant, filesystem_scope: "workspace_only"};
    const selected = resolveProjectConversation({project_ref: context.project_ref, available: [scoped]});
    assert.deepEqual(selected.context, scoped);
    const identity = projectConversationIdentity({context: selected.context}) as any;
    assert.equal(identity.host_config.default_permissions, identity.permissions_profile);
    assert.equal(identity.host_store_key, `${context.project_ref}.local`);
    const profile = identity.host_config.permissions[identity.permissions_profile];
    assert.equal(profile.filesystem[":root"], "deny");
    assert.equal(profile.filesystem[":workspace_roots"]["."], grant === "workspace_write" ? "write" : "read");
    assert.equal(profile.filesystem[":tmpdir"], "deny");
    assert.equal(profile.network.enabled, false);
    assert.equal(identity.host_config.shell_environment_policy.inherit, "none");
    assert.deepEqual(identity.host_config.shell_environment_policy.include_only, ["PATH"]);
    assert.equal(identity.host_config.shell_environment_policy.experimental_use_profile, false);
    assert.deepEqual(identity.host_config.shell_environment_policy.set, {PATH: "/usr/bin:/bin:/usr/sbin:/sbin"});
    assert.deepEqual(identity.host_config.skills, {include_instructions: false});
    assert.equal(identity.host_config.project_doc_max_bytes, 0);
    assert.throws(() => resolveProjectConversation({project_ref: context.project_ref,
      available: [context], session_context: scoped}), /grant changed/);
    assert.throws(() => resolveProjectConversation({project_ref: context.project_ref,
      available: [scoped], session_context: context}), /grant changed/);
  }
  assert.throws(() => projectConversationIdentity({context: {...context, filesystem_scope: "anything"}}));
  assert.equal(projectConversationIdentity({context}).permissions_profile, undefined);
  assert.equal(projectConversationIdentity({context}).host_config, undefined);
  assert.equal(projectConversationIdentity({context}).host_store_key, undefined);
});

test("private native store follows the authorized workspace and App owner, not a source topic or grant", () => {
  const bound = {...context, audience: "bound_owner", filesystem_scope: "workspace_only",
    binding_id: "b".repeat(24), provider_ref: "c".repeat(24), operator_ref: "d".repeat(24), source_ref: "e".repeat(24)};
  const key = projectConversationIdentity({context: bound}).host_store_key;
  assert.equal(key, [bound.project_ref, bound.binding_id, bound.provider_ref, bound.operator_ref].join("."));
  for (const patch of [{source_ref: "f".repeat(24)}, {grant: "workspace_write"}]) {
    assert.equal(projectConversationIdentity({context: {...bound, ...patch}}).host_store_key, key);
  }
  for (const field of ["project_ref", "binding_id", "provider_ref", "operator_ref"]) {
    assert.notEqual(projectConversationIdentity({context: {...bound, [field]: "f".repeat(24)}}).host_store_key, key);
  }
});
