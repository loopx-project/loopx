import assert from "node:assert/strict";
import test from "node:test";
import {leaseWriteRepository, leaseRepositoryRejection, repositoryScopesMayOverlap} from "../../loopx/control_plane/work_items/task_lease_repository.ts";
import {evaluateTaskLeaseAcquireDecision, materializeTaskLeaseAcquire} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import {evaluateCoordinationTerminalFence, COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/todo_lifecycle_decision.ts";

const repository = "git:github.com/team/a";
function input(task_repository: string | null = repository, other_repository: string | null = "git:github.com/team/b") {
  return {handoff_mode: "hard_lease", registered_agents: ["agent-a", "agent-b"],
    todo: {todo_id: "todo_a", status: "open", claimed_by: "agent-a", excluded_agents: [], task_repository}, lease: null,
    other_leases: [{todo_id: "todo_b", active: true, effective: true, write_scopes: ["tests/**"], write_repository: other_repository}],
    command: {owner: "agent-a", idempotency_key: "turn-a", ttl_seconds: 60, expected_version: 0, write_scopes: ["tests/**"]}};
}

test("repository scopes require two distinct known identities and never infer historical namespaces", () => {
  assert.equal(repositoryScopesMayOverlap(repository, "git:github.com/team/b"), false);
  for (const alias of [repository, "git:github.com/Team/A", null, undefined]) assert.equal(repositoryScopesMayOverlap(repository, alias), true);
  assert.equal(leaseRepositoryRejection({task_repository: repository}, {}), null);
  assert.equal(leaseRepositoryRejection({task_repository: null}, {write_repository: repository}), "lease_repository_divergence");
  assert.equal(leaseRepositoryRejection({task_repository: "https://github.com/team/a.git"}, {write_repository: repository}), null);
});

test("frozen namespace codec rejects malformed, noncanonical and empty identities", () => {
  for (const value of ["", " ", "https://github.com/team/a.git", "git:github.com/team/../a", false, [], "git:github.com/team\\a"]) {
    assert.throws(() => leaseWriteRepository(value));
    assert.throws(() => evaluateTaskLeaseAcquireDecision({...input(), other_leases: [{...input().other_leases[0], write_repository: value}]}));
  }
});

test("only canonical Todo facts determine a new namespace; caller fields cannot override it", () => {
  const request = input("git@github.com:team/a.git");
  const decision = evaluateTaskLeaseAcquireDecision({...request, write_repository: "git:github.com/team/evil",
    command: {...request.command, task_repository: "git:github.com/team/evil", write_repository: "git:github.com/team/evil"}});
  assert.equal(decision.outcome, "apply");
  const lease = materializeTaskLeaseAcquire({goal_id: "goal", todo_id: "todo_a"}, request.command, decision, new Date("2026-09-27T00:00:00Z"));
  assert.equal(lease.write_repository, repository);
  const unknown = input(null); unknown.other_leases = [];
  assert.equal(Object.hasOwn(evaluateTaskLeaseAcquireDecision(unknown).next_lease!, "write_repository"), false);
  const noScopes = {...input(null), command: {...input().command, write_scopes: []}};
  assert.equal(evaluateTaskLeaseAcquireDecision(noScopes).outcome, "apply");
});

test("terminal execution fence rejects repository drift even with the exact holder key/version", () => {
  const request = {schema_version: COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA,
    todo: {...input().todo, role: "agent", task_class: "implementation", task_repository: "git:github.com/team/b"},
    registered_agents: ["agent-a"], actor_agent_id: "agent-a", handoff_mode: "hard_lease",
    lease: {present: true, active: true, status: "active", owner: "agent-a", idempotency_key: "turn-a",
      version: 1, lease_epoch: 1, write_scopes: ["tests/**"], acquire_ttl_seconds: 60, write_repository: repository},
    lease_idempotency_key: "turn-a", lease_expected_version: 1, allow_user_gate_auto_acquire: false,
    delegated_authority: false, require_active_when_fence_supplied: true};
  assert.equal(evaluateCoordinationTerminalFence(request).code, "lease_repository_divergence");
  assert.equal(evaluateCoordinationTerminalFence({...request, todo: {...request.todo, task_repository: repository}}).code, "terminal_fence_verified");
});
