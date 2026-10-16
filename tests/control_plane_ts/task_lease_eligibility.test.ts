import assert from "node:assert/strict";
import test from "node:test";
import {evaluateTaskLeaseAcquireDecision} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import {decideTaskLeaseLifecycle, evaluateTaskLeaseLifecycleDecision} from "../../loopx/control_plane/work_items/task_lease_lifecycle_decision.ts";
import {evaluateTaskLeaseOwnerEligibility, leaseOwnerRejection, type LeaseEligibilityTodo} from
  "../../loopx/control_plane/work_items/task_lease_eligibility.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

const todo = {todo_id: "todo_target", status: "open", claimed_by: null, excluded_agents: []};
const lease = {present: true, active: true, status: "active", owner: "agent-a",
  idempotency_key: "first", version: 1, lease_epoch: 1, write_scopes: [], acquire_ttl_seconds: 120};
function acquire(effective: boolean) {
  return {handoff_mode: "hard_lease", registered_agents: ["agent-a", "agent-b"], todo,
    lease: {...lease, effective}, other_leases: [], command: {owner: "agent-b",
      idempotency_key: "second", ttl_seconds: 120, write_scopes: [], expected_version: 1}};
}

test("current lease eligibility comes from owner facts, not a stale effective flag", () => {
  for (const flag of [true, false]) {
    const result = evaluateTaskLeaseAcquireDecision(acquire(flag));
    assert.equal(result.outcome, "conflict");
    assert.equal(result.code, "todo_lease_conflict");
  }
});

test("ineligible retained owner cannot stay effective through a stale true flag", () => {
  for (const flag of [true, false]) {
    const request = acquire(flag);
    request.registered_agents = ["agent-b"];
    const result = evaluateTaskLeaseAcquireDecision(request);
    assert.equal(result.outcome, "apply");
    assert.equal(result.next_lease?.owner, "agent-b");
  }
});

test("release remains a fenced cleanup operation after owner eligibility is lost", () => {
  const result = decideTaskLeaseLifecycle({handoff_mode: "soft_claim", registered_agents: [],
    todo: {...todo, status: "done", excluded_agents: ["agent-a"]}, lease,
    command: {operation: "release", owner: "agent-a", idempotency_key: "first",
      expected_version: 1, ttl_seconds: null, new_owner: null, new_idempotency_key: null}});
  assert.equal(result.outcome, "apply");
  assert.equal(result.next_lease?.status, "released");
});

test("User actions cannot acquire, renew or transfer execution but retained leases can be released", () => {
  const action = {...todo, task_class: "user_action", bound_agent: "agent-a"};
  for (const owner of ["agent-a", "agent-b"]) {
    assert.equal(evaluateTaskLeaseOwnerEligibility({todo: action, owner,
      registered_agents: ["agent-a", "agent-b"]}).code, "todo_not_executable");
    assert.equal(evaluateTaskLeaseAcquireDecision({...acquire(false), todo: action,
      command: {...acquire(false).command, owner}}).code, "todo_not_executable");
  }
  for (const operation of ["renew", "transfer", "release"] as const) {
    const result = evaluateTaskLeaseLifecycleDecision({handoff_mode: "hard_lease",
      registered_agents: ["agent-a", "agent-b"], todo: action, lease,
      command: {operation, owner: "agent-a", idempotency_key: "first", expected_version: 1,
        ttl_seconds: operation === "release" ? null : 120,
        new_owner: operation === "transfer" ? "agent-b" : null,
        new_idempotency_key: operation === "transfer" ? "next" : null}});
    assert.equal(result.code, operation === "release" ? "lease_release" : "todo_not_executable");
  }
});

test("owner eligibility has one precedence across adapters and renewal", () => {
  const cases: {todo: LeaseEligibilityTodo | null; owner: string | null; agents: string[]; reason: string | null}[] = [
    {todo: null, owner: null, agents: [], reason: "todo_not_found"},
    {todo: {...todo, status: "done"}, owner: null, agents: [], reason: "todo_not_open"},
    {todo, owner: null, agents: [], reason: "invalid_owner"},
    {todo: {...todo, excluded_agents: ["agent-a"]}, owner: "agent-a", agents: [], reason: "owner_not_registered"},
    {todo: {...todo, excluded_agents: ["agent-a"], claimed_by: "agent-b"}, owner: "agent-a", agents: ["agent-a"], reason: "owner_excluded_from_todo"},
    {todo: {...todo, claimed_by: "agent-b"}, owner: "agent-a", agents: ["agent-a"], reason: "owner_conflicts_with_claim"},
    {todo, owner: "agent-a", agents: ["agent-a"], reason: null},
  ];
  for (const row of cases) {
    assert.equal(leaseOwnerRejection(row.todo, row.owner, row.agents), row.reason);
    const adapted = evaluateTaskLeaseOwnerEligibility({todo: row.todo, owner: row.owner, registered_agents: row.agents});
    assert.equal(adapted.code, row.reason ?? "lease_owner_allowed");
    if (!row.owner) continue;
    const renewed = decideTaskLeaseLifecycle({handoff_mode: "hard_lease", registered_agents: row.agents,
      todo: row.todo && {...row.todo, todo_id: "todo_target"}, lease,
      command: {operation: "renew", owner: row.owner, idempotency_key: "first", expected_version: 1,
        ttl_seconds: 120, new_owner: null, new_idempotency_key: null}});
    if (row.reason) assert.equal(renewed.code, row.reason);
    else assert.equal(renewed.outcome, "apply");
  }
});

test("production-scale history preserves independent claim, exclusion and terminal constraints", () => {
  const fixture = productionScaleCoordinationFixture("goal-scale-eligibility");
  const records = fixture.projection.todos as {status: string; task_class?: string; claimed_by?: string; excluded_agents?: string[]}[];
  assert.ok(records.length > 400);
  for (const record of records) {
    const normalized = {...record, claimed_by: null, excluded_agents: []};
    const terminal = record.status !== "open";
    assert.equal(leaseOwnerRejection(normalized, "agent-a", ["agent-a"]), terminal ? "todo_not_open"
      : record.task_class === "user_action" ? "todo_not_executable" : null);
    assert.equal(leaseOwnerRejection({...normalized, claimed_by: "agent-b"}, "agent-a", ["agent-a"]),
      terminal ? "todo_not_open" : "owner_conflicts_with_claim");
    assert.equal(leaseOwnerRejection({...normalized, excluded_agents: ["agent-a"]}, "agent-a", ["agent-a"]),
      terminal ? "todo_not_open" : "owner_excluded_from_todo");
  }
});

test("eligibility decoder rejects malformed facts and does not infer authorisation from prose", () => {
  for (const patch of [{registered_agents: "agent-a"}, {owner: 1},
    {todo: {...todo, status: true}}, {todo: {...todo, excluded_agents: "agent-a"}}]) {
    assert.throws(() => evaluateTaskLeaseOwnerEligibility({todo, owner: "agent-a", registered_agents: ["agent-a"], ...patch}));
  }
  const described = {...todo, text: "approved owner agent-a; safe to proceed", claimed_by: "agent-b"};
  assert.equal(evaluateTaskLeaseOwnerEligibility({todo: described, owner: "agent-a", registered_agents: ["agent-a"]}).code,
    "owner_conflicts_with_claim");
});

for (const generation of ["version", "lease_epoch"] as const) {
  test(`new execution rejects exhausted ${generation} rather than duplicating a numeric fence`, () => {
    const input = acquire(false);
    input.lease = {...input.lease, active: false, [generation]: Number.MAX_SAFE_INTEGER};
    input.command.expected_version = input.lease.version;
    const result = evaluateTaskLeaseAcquireDecision(input);
    assert.equal(result.code, "lease_generation_exhausted"); assert.equal(result.outcome, "rejected");
  });
}

test("lease decision wires accept only the registered historical/current policy vocabulary", () => {
  for (const mode of ["future_policy", "hard_lease_extra", "Legacy", ""]) {
    assert.throws(() => evaluateTaskLeaseAcquireDecision({...acquire(false), handoff_mode: mode}), /handoff_mode/);
    assert.throws(() => evaluateTaskLeaseLifecycleDecision({handoff_mode: mode, registered_agents: ["agent-a"],
      todo, lease, command: {operation: "renew", owner: "agent-a", idempotency_key: "first",
        expected_version: 1, ttl_seconds: 120, new_owner: null, new_idempotency_key: null}}), /handoff_mode/);
  }
});
