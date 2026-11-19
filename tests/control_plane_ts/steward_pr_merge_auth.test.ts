import assert from "node:assert/strict";
import test from "node:test";
import {authorizeStewardCreation} from "../../loopx/control_plane/collaboration/conversation_binding.ts";

function stewardContext() {
  return {
    kind: "bound_steward",
    audience: "bound_owner",
    grant: "portfolio_read",
    binding_id: "a".repeat(24),
    source_ref: "b".repeat(24),
    provider_ref: "c".repeat(24),
    operator_ref: "d".repeat(24),
    project_ref: "e".repeat(24),
    workspace_path: "/workspace",
    goal_ids: ["goal-alpha"],
  };
}

function prMergeProposal(overrides: Record<string, unknown> = {}, contextOverrides: Record<string, unknown> = {}) {
  return {
    action_kind: "pr.merge",
    created_at: "2030-01-01T09:00:00Z",
    normalized_parameters: {
      repository: "org/repo",
      pr_number: 42,
      merge_method: "squash",
      admin_bypass: true,
      ...overrides,
    },
    context: {
      kind: "manager",
      goal_id: "loopx-manager",
      binding_id: "a".repeat(24),
      source_ref: "b".repeat(24),
      provider_ref: "c".repeat(24),
      operator_ref: "d".repeat(24),
      project_ref: "e".repeat(24),
      expires_at: "2030-01-01T09:15:00Z",
      ...contextOverrides,
    },
  };
}

test("authorizeStewardCreation admits pr.merge only for an authorized repository and audience within expiry", () => {
  const result = authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal(),
    now: "2030-01-01T09:05:00Z",
    operation: "confirm",
    repository_authorized: true,
  });
  assert.deepEqual(result, {authorized: true});

  assert.throws(() => authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal(),
    now: "2030-01-01T09:05:00Z",
    operation: "confirm",
    repository_authorized: false,
  }));
  assert.throws(() => authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal(),
    now: "2030-01-01T09:05:00Z",
    operation: "stop",
    repository_authorized: true,
  }));
  assert.throws(() => authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal(),
    now: "2030-01-01T09:05:00Z",
    operation: "resume",
    repository_authorized: true,
  }));
  assert.throws(() => authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal({}, {operator_ref: "f".repeat(24)}),
    now: "2030-01-01T09:05:00Z",
    operation: "confirm",
    repository_authorized: true,
  }));
  assert.throws(() => authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal(),
    now: "2030-01-01T09:16:00Z",
    operation: "confirm",
    repository_authorized: true,
  }));
  assert.throws(() => authorizeStewardCreation({
    context: stewardContext(),
    proposal: prMergeProposal({pr_number: 0}),
    now: "2030-01-01T09:05:00Z",
    operation: "confirm",
    repository_authorized: true,
  }));
});
