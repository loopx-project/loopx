import assert from "node:assert/strict";
import test from "node:test";

import {
  DELIVERY_WORKSPACE_REQUEST_SCHEMA,
  evaluateDeliveryWorkspace,
  normalizeDeliveryWorkspaceSnapshot,
} from "../../loopx/control_plane/agents/delivery_workspace.ts";

test("builds typed git and local-goal workspace snapshots", () => {
  assert.deepEqual(evaluateDeliveryWorkspace({
    schema_version: DELIVERY_WORKSPACE_REQUEST_SCHEMA,
    operation: "build",
    observation: {
      workspace_identity: "git:GitHub.com/example/loopx.git",
      identity_kind: "git_repository",
      workspace_revision_digest:
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      repository_source: "current_git_origin",
      workspace_kind: "independent_git_worktree",
      peer_independent_worktree_required: true,
    },
  }), {
    schema_version: "loopx_delivery_workspace_result_v0",
    workspace: {
      schema_version: "delivery_workspace_v1",
      workspace_identity: "git:github.com/example/loopx",
      identity_kind: "git_repository",
      task_repository: "git:github.com/example/loopx",
      workspace_revision_digest:
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      repository_source: "current_git_origin",
      workspace_kind: "independent_git_worktree",
      peer_independent_worktree_required: true,
    },
  });

  assert.deepEqual(evaluateDeliveryWorkspace({
    schema_version: DELIVERY_WORKSPACE_REQUEST_SCHEMA,
    operation: "build",
    observation: {
      workspace_identity: "loopx:local-goal",
      identity_kind: "local_goal",
      repository_source: "goal_id_fallback",
      workspace_kind: "local_goal_workspace",
      peer_independent_worktree_required: false,
    },
  }), {
    schema_version: "loopx_delivery_workspace_result_v0",
    workspace: {
      schema_version: "delivery_workspace_v1",
      workspace_identity: "loopx:local-goal",
      identity_kind: "local_goal",
      task_repository: null,
      repository_source: "goal_id_fallback",
      workspace_kind: "local_goal_workspace",
      peer_independent_worktree_required: false,
    },
  });
});

test("normalizes legacy git snapshots without weakening peer policy", () => {
  assert.deepEqual(normalizeDeliveryWorkspaceSnapshot({
    schema_version: "delivery_workspace_v0",
    task_repository: "git:github.com/example/loopx",
    repository_source: "current_git_origin",
    workspace_kind: "canonical_checkout",
    peer_independent_worktree_required: true,
  }), {
    schema_version: "delivery_workspace_v1",
    workspace_identity: "git:github.com/example/loopx",
    identity_kind: "git_repository",
    task_repository: "git:github.com/example/loopx",
    repository_source: "current_git_origin",
    workspace_kind: "canonical_checkout",
    peer_independent_worktree_required: true,
  });
});

test("rejects local workspaces that claim peer-independent delivery", () => {
  assert.equal(normalizeDeliveryWorkspaceSnapshot({
    schema_version: "delivery_workspace_v1",
    workspace_identity: "loopx:local-goal",
    identity_kind: "local_goal",
    task_repository: null,
    repository_source: "goal_id_fallback",
    workspace_kind: "local_goal_workspace",
    peer_independent_worktree_required: true,
  }), null);
});

test("workspace normalization is immutable and fails closed on contradictions", () => {
  const candidate = {
    schema_version: "delivery_workspace_v1",
    workspace_identity: "git:github.com/example/loopx",
    identity_kind: "git_repository",
    task_repository: "git:github.com/example/other",
    repository_source: "current_git_origin",
    workspace_kind: "canonical_checkout",
    peer_independent_worktree_required: false,
  };
  const before = structuredClone(candidate);
  assert.equal(normalizeDeliveryWorkspaceSnapshot(candidate), null);
  assert.deepEqual(candidate, before);

  assert.throws(
    () => evaluateDeliveryWorkspace({
      schema_version: DELIVERY_WORKSPACE_REQUEST_SCHEMA,
      operation: "build",
      observation: {
        workspace_identity: "loopx:local-goal",
        identity_kind: "local_goal",
        repository_source: "goal_id_fallback",
        workspace_kind: "local_goal_workspace",
        peer_independent_worktree_required: "false",
      },
    }),
    /peer_independent_worktree_required must be a boolean/,
  );
});

test("peer isolation preserves local delivery and repository/owner boundaries", () => {
  const local = {
    schema_version: "delivery_workspace_v1", workspace_identity: "loopx:research",
    identity_kind: "local_goal", task_repository: null,
    repository_source: "goal_id_fallback", workspace_kind: "local_goal_workspace",
    peer_independent_worktree_required: false,
  };
  const git = {
    ...local, workspace_identity: "git:github.com/example/project",
    identity_kind: "git_repository", task_repository: "git:github.com/example/project",
    workspace_kind: "canonical_checkout",
  };
  for (const [workspace, explicit, repository, required, accepted] of [
    [local, null, null, false, true],
    [local, true, null, true, false],
    [local, null, "git:github.com/example/project", true, false],
    [null, null, null, true, false],
    [git, null, null, true, true],
    [git, false, null, false, true],
    [git, null, "git:github.com/example/other", true, false],
  ] as const) {
    const result = evaluateDeliveryWorkspace({
      schema_version: DELIVERY_WORKSPACE_REQUEST_SCHEMA, operation: "isolation",
      workspace, multi_agent_goal: true, explicit_peer_worktree_requirement: explicit,
      task_repository: repository,
    });
    assert.equal(result.peer_independent_worktree_required, required);
    assert.equal(result.workspace !== null, accepted);
    if (result.workspace) {
      assert.equal((result.workspace as Record<string, unknown>).peer_independent_worktree_required, required);
    }
  }
  assert.equal(local.peer_independent_worktree_required, false);
});
