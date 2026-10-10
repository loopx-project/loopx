import assert from "node:assert/strict";
import test from "node:test";

import {
  COORDINATION_TODO_TERMINAL_DECISION_REQUEST_SCHEMA,
  COORDINATION_TODO_TERMINAL_DECISION_RESULT_SCHEMA,
  evaluateCoordinationTodoTerminalDecision,
  COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA,
  COORDINATION_TODO_MUTATION_DECISION_RESULT_SCHEMA,
  COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA,
  evaluateCoordinationTodoMutationDecision,
  evaluateCoordinationTerminalFence,
  evaluateTodoOwnershipGate,
} from "../../loopx/control_plane/coordination/todo_lifecycle_decision.ts";

function request(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: COORDINATION_TODO_TERMINAL_DECISION_REQUEST_SCHEMA,
    command: "complete",
    handoff_mode: "legacy",
    registered_agents: ["agent-a", "agent-b"],
    lifecycle_grants: [],
    todo: {
      todo_id: "todo_target",
      status: "open",
      role: "agent",
      task_class: "advancement_task",
      claimed_by: "agent-a",
      excluded_agents: [],
      bound_agent: null,
      blocks_agent: null,
      decision_scope: null,
      required_decision_scopes: [],
      unblocks_todo_id: null,
    },
    decision_target: null,
    lease: null,
    actor_agent_id: "agent-a",
    authority_action: "complete",
    authority_reason: null,
    decision_outcome: null,
    lease_idempotency_key: null,
    lease_expected_version: null,
    allow_user_gate_auto_acquire: false,
    ...overrides,
  };
}

function mutation(overrides: Record<string, unknown> = {}) {
  return request({
    schema_version: COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA,
    command: "update", authority_action: "update", requested_claimed_by: null,
    clear_claim: false, ownership_mutation: false, ...overrides,
  });
}

test("normalized snapshot corruption precedes Todo absence, actor denial and replay for every verb", () => {
  const holder = {present: true, active: true, status: "active", owner: "agent-a",
    idempotency_key: "holder", version: 3, lease_epoch: 1, write_scopes: []};
  for (const mode of ["legacy", "soft_claim", "hard_lease"]) {
    for (const command of ["claim", "update", "complete", "supersede"]) {
      const terminal = command === "complete" || command === "supersede";
      const build = terminal ? request : mutation;
      const evaluate = terminal ? evaluateCoordinationTodoTerminalDecision : evaluateCoordinationTodoMutationDecision;
      for (const lease of [{...holder, present: false}, {...holder, status: "released"}]) {
        for (const todo of [request().todo, {...request().todo, status: "done"}, null]) {
          for (const actor of ["agent-a", "unknown"]) {
            const result = evaluate(build({command, authority_action: command, handoff_mode: mode,
              lease, todo, actor_agent_id: actor}));
            assert.equal(result.schema_version, terminal ? COORDINATION_TODO_TERMINAL_DECISION_RESULT_SCHEMA
              : COORDINATION_TODO_MUTATION_DECISION_RESULT_SCHEMA);
            assert.equal(result.outcome, "rejected");
            assert.equal(result.code, "invalid_lease_snapshot");
            assert.equal(result.authority_mode, null);
            assert.equal(result.ownership_gate, "not_required");
            assert.equal(result.lease_fence, "not_required");
            assert.equal(result.idempotent, false);
            assert.equal(result.next_todo_status, null);
            assert.equal(result.next_lease, null);
          }
        }
      }
    }
  }
});

test("explicit missing Todo is a typed refusal; missing or malformed wire fields remain errors", () => {
  for (const mode of ["legacy", "soft_claim", "hard_lease"]) {
    for (const command of ["claim", "update", "complete", "supersede"]) {
      const terminal = command === "complete" || command === "supersede";
      const build = terminal ? request : mutation;
      const evaluate = terminal ? evaluateCoordinationTodoTerminalDecision : evaluateCoordinationTodoMutationDecision;
      const result = evaluate(build({command, authority_action: command, handoff_mode: mode,
        todo: null, actor_agent_id: "unknown"}));
      assert.equal(result.outcome, "rejected");
      assert.equal(result.code, "todo_not_found");
      assert.equal(result.idempotent, false);
      assert.equal(result.next_todo_status, null);
      for (const todo of [undefined, {}, "missing"]) assert.throws(() => evaluate(build({todo})));
      assert.throws(() => evaluate(build({todo: null, command: "not-a-command"})));
      assert.throws(() => evaluate(build({todo: null, handoff_mode: "not-a-mode"})));
      assert.throws(() => evaluate(build({todo: null, lease: {active: "true"}})));
    }
  }
});

test("snapshot admission preserves inactive holders and claim-neutral mutation effects", () => {
  const holder = {present: true, active: true, status: "active", owner: "agent-a",
    idempotency_key: "holder", version: 3, lease_epoch: 1, write_scopes: []};
  for (const mode of ["legacy", "soft_claim", "hard_lease"]) {
    for (const lease of [null, holder, {...holder, active: false, status: "expired"},
      {...holder, active: false, status: "released"}]) {
      const result = evaluateCoordinationTodoMutationDecision(mutation({handoff_mode: mode, lease}));
      assert.equal(result.outcome, "apply");
      assert.equal(result.next_todo_claimed_by, "agent-a");
      assert.equal(result.next_lease, null, "admission does not edit a lease");
    }
  }
});

test("bound User action closure is administrative, not a fabricated execution lease", () => {
  const todo = {...request().todo, role: "user", task_class: "user_action",
    claimed_by: null, bound_agent: "agent-a"};
  const base = request({todo, handoff_mode: "hard_lease", decision_outcome: "cancel"});
  for (const status of ["open", "blocked", "deferred"]) {
    const result = evaluateCoordinationTodoTerminalDecision({...base, todo: {...todo, status}});
    assert.equal(result.outcome, "apply");
    assert.equal(result.lease_fence, "not_required");
    assert.equal(result.next_lease, null);
  }
  for (const [override, code] of [
    [{actor_agent_id: null}, "actor_required"],
    [{actor_agent_id: "unknown"}, "actor_not_registered"],
    [{actor_agent_id: "agent-b"}, "bound_agent_mismatch"],
    [{todo: {...todo, excluded_agents: ["agent-a"]}}, "actor_excluded"],
    [{todo: {...todo, bound_agent: null}}, "handoff_mode_requires_lease"],
    [{todo: {...todo, claimed_by: "agent-b"}}, "claim_owner_mismatch"],
    [{lease_idempotency_key: "expired-key"}, "handoff_mode_requires_lease"],
    [{command: "supersede", authority_action: "supersede"}, "handoff_mode_requires_lease"],
  ] as const) assert.equal(evaluateCoordinationTodoTerminalDecision({...base, ...override}).code, code);
  const lease = {present: true, active: true, status: "active", owner: "agent-a",
    idempotency_key: "holder", version: 3, lease_epoch: 1, write_scopes: []};
  assert.equal(evaluateCoordinationTodoTerminalDecision({...base, lease}).code, "lease_fence_required");
  assert.equal(evaluateCoordinationTodoTerminalDecision({...base, lease,
    lease_idempotency_key: "holder", lease_expected_version: 2}).code, "version_mismatch");
  assert.equal(evaluateCoordinationTodoTerminalDecision({...base, lease: {...lease, owner: "agent-b"}}).outcome,
    "rejected", "an active foreign holder cannot be bypassed");
  assert.equal(evaluateCoordinationTodoTerminalDecision({...base, lease: {...lease, owner: "agent-b"},
    lease_idempotency_key: "holder", lease_expected_version: 3}).code, "lease_cas_mismatch");
  assert.equal(evaluateCoordinationTodoTerminalDecision({...base,
    lease: {...lease, active: false, status: "expired"}}).next_lease?.status, "released");
});

test("update admission shares actor rules without inventing terminal effects", () => {
  const base = mutation({ todo: { ...request().todo as object, claimed_by: null } });
  for (const mode of ["legacy", "soft_claim", "hard_lease"]) {
    const edited = evaluateCoordinationTodoMutationDecision({ ...base, handoff_mode: mode });
    assert.equal(edited.outcome, "apply");
    assert.equal(edited.next_todo_status, "open");
    assert.equal(edited.next_todo_claimed_by, null);
    assert.equal(edited.next_lease, null);
    assert.equal(edited.authority_mode, "registered_peer_actor");
  }
  for (const [override, code] of [
    [{ actor_agent_id: null }, "actor_required"],
    [{ actor_agent_id: "unknown" }, "actor_not_registered"],
    [{ todo: { ...base.todo as object, excluded_agents: ["agent-a"] } }, "actor_excluded"],
    [{ todo: { ...base.todo as object, bound_agent: "agent-b" } }, "bound_agent_mismatch"],
    [{ todo: { ...base.todo as object, claimed_by: "agent-b" } }, "claim_owner_mismatch"],
  ] as const) {
    const rejected = evaluateCoordinationTodoMutationDecision({ ...base, ...override });
    assert.equal(rejected.code, code);
    assert.equal(rejected.outcome, "rejected");
  }
});

test("ownership changes use the same holder gate as the locked writer", () => {
  const base = mutation({ handoff_mode: "hard_lease", ownership_mutation: true, clear_claim: true });
  assert.equal(evaluateCoordinationTodoMutationDecision(base).code, "handoff_mode_requires_lease");
  const lease = { present: true, active: true, status: "active", owner: "agent-a",
    idempotency_key: "execution-a", version: 3, lease_epoch: 5, write_scopes: [] };
  const cleared = evaluateCoordinationTodoMutationDecision({ ...base, lease });
  assert.equal(cleared.outcome, "apply");
  assert.equal(cleared.next_todo_claimed_by, null);
  assert.equal(cleared.next_lease, null, "ownership admission must not release the holder lease");
  assert.equal(cleared.ownership_gate, evaluateTodoOwnershipGate({
    handoff_mode: "hard_lease", ownership_mutation: true, authority_mode: "registered_peer_actor",
  }).ownership_gate);
  assert.equal(evaluateCoordinationTodoMutationDecision({ ...base, lease: { ...lease, owner: "agent-b" } }).code,
    "handoff_mode_requires_lease");
});

test("delegated update is action/reason bound, not a general ownership bypass", () => {
  const base = mutation({ actor_agent_id: "agent-b", handoff_mode: "hard_lease",
    ownership_mutation: true, requested_claimed_by: "agent-b", authority_action: "reassign",
    lifecycle_grants: [{agent_id: "agent-b", actions: ["reassign"], requires_reason: true}] });
  assert.equal(evaluateCoordinationTodoMutationDecision(base).code, "delegation_reason_required");
  const accepted = evaluateCoordinationTodoMutationDecision({ ...base, authority_reason: "recover work" });
  assert.equal(accepted.ownership_gate, "delegated_override");
  assert.equal(accepted.next_todo_claimed_by, "agent-b");
  assert.equal(evaluateCoordinationTodoMutationDecision({ ...base, authority_action: "update",
    authority_reason: "recover work" }).code, "delegation_action_not_granted");
});

test("mutation protocol cannot masquerade as completion or smuggle boolean strings", () => {
  const minimal = mutation();
  for (const key of ["allow_user_gate_auto_acquire", "decision_target", "decision_outcome",
    "lease_idempotency_key", "lease_expected_version"]) delete minimal[key];
  assert.equal(evaluateCoordinationTodoMutationDecision(minimal).outcome, "apply");
  assert.throws(() => evaluateCoordinationTodoMutationDecision(mutation({ command: "complete" })));
  assert.throws(() => evaluateCoordinationTodoTerminalDecision(mutation()));
  assert.throws(() => evaluateCoordinationTodoMutationDecision(mutation({ ownership_mutation: "false" })));
  assert.equal(evaluateCoordinationTodoMutationDecision(mutation({ command: "claim",
    authority_action: "claim", requested_claimed_by: "agent-b" })).code, "claim_actor_mismatch");
});

test("executor reclaim remains internal and preserves actor rejection precedence", () => {
  const reclaim = (actor: string) => mutation({
    actor_agent_id: actor, authority_action: "reclaim", ownership_mutation: true,
    clear_claim: true, handoff_mode: "hard_lease",
    lifecycle_grants: [{ agent_id: actor, actions: ["reclaim"], requires_reason: false }],
  });
  const accepted = evaluateCoordinationTodoMutationDecision(reclaim("agent-b"));
  assert.equal(accepted.outcome, "apply");
  assert.equal(accepted.ownership_gate, "delegated_override");
  assert.equal(accepted.next_todo_claimed_by, null);
  assert.equal(accepted.next_lease, null);
  assert.equal(evaluateCoordinationTodoMutationDecision(reclaim("agent-z")).code,
    "actor_not_registered");
  assert.equal(evaluateCoordinationTodoMutationDecision({ ...reclaim("agent-b"),
    todo: { ...request().todo as object, excluded_agents: ["agent-b"] },
  }).code, "actor_excluded");
  for (const change of [
    { command: "claim" }, { clear_claim: false }, { ownership_mutation: false },
    { requested_claimed_by: "agent-b" },
    { lifecycle_grants: [{ agent_id: "agent-a", actions: ["reclaim"], requires_reason: false }] },
    { lifecycle_grants: [{ agent_id: "agent-b", actions: ["update", "reclaim"], requires_reason: false }] },
  ]) assert.throws(() => evaluateCoordinationTodoMutationDecision({ ...reclaim("agent-b"), ...change }));
  // The public grant decoder and terminal wire must not gain reclaim authority.
  assert.throws(() => evaluateCoordinationTodoMutationDecision({ ...reclaim("agent-b"), authority_action: "update" }));
  assert.throws(() => evaluateCoordinationTodoTerminalDecision(request({
    lifecycle_grants: reclaim("agent-b").lifecycle_grants,
  })));
});

test("in-process preauthorized fence never completes or attributes a Todo", () => {
  const base = request({ schema_version: COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA,
    actor_agent_id: null, delegated_authority: false, require_active_when_fence_supplied: false,
    lease_idempotency_key: "old-key" });
  const accepted = evaluateCoordinationTerminalFence(base);
  assert.equal(accepted.code, "terminal_fence_not_required");
  assert.equal(accepted.next_todo_status, null);
  assert.equal(accepted.authority_mode, null);
  assert.equal(evaluateCoordinationTerminalFence({ ...base, require_active_when_fence_supplied: true }).code,
    "lease_not_active");
  assert.throws(() => evaluateCoordinationTerminalFence({ ...base, delegated_authority: "true" }));
});

test("terminal decision owns complete and supersede authority", () => {
  for (const command of ["complete", "supersede"]) {
    const decided = evaluateCoordinationTodoTerminalDecision(request({
      command,
      authority_action: command,
    }));
    assert.equal(decided.outcome, "apply");
    assert.equal(decided.code, "terminal_transition");
    assert.equal(decided.authority_mode, "registered_peer_actor");
    assert.equal(decided.next_todo_status, "done");
  }
});

test("terminal authority rejects missing, unknown, excluded, bound, and foreign actors", () => {
  const cases: Array<[Record<string, unknown>, string]> = [
    [{ actor_agent_id: null }, "actor_required"],
    [{ actor_agent_id: "agent-c" }, "actor_not_registered"],
    [{ todo: { ...request().todo as object, excluded_agents: ["agent-a"] } }, "actor_excluded"],
    [{ todo: { ...request().todo as object, role: "user", claimed_by: null,
      bound_agent: "agent-b" } }, "bound_agent_mismatch"],
    [{ actor_agent_id: "agent-b" }, "claim_owner_mismatch"],
  ];
  for (const [overrides, code] of cases) {
    const decided = evaluateCoordinationTodoTerminalDecision(request(overrides));
    assert.equal(decided.outcome, "rejected");
    assert.equal(decided.code, code);
  }
});

test("delegated terminal authority is explicit and reason-bound", () => {
  const base = {
    actor_agent_id: "agent-b",
    lifecycle_grants: [{
      agent_id: "agent-b",
      actions: ["complete"],
      requires_reason: true,
    }],
  };
  assert.equal(evaluateCoordinationTodoTerminalDecision(request(base)).code,
    "delegation_reason_required");
  const accepted = evaluateCoordinationTodoTerminalDecision(request({
    ...base,
    authority_reason: "recover abandoned owner",
  }));
  assert.equal(accepted.outcome, "apply");
  assert.equal(accepted.authority_mode, "delegated_orchestration_override");

  const wrongAction = evaluateCoordinationTodoTerminalDecision(request({
    ...base,
    authority_action: "supersede",
    authority_reason: "recover abandoned owner",
  }));
  assert.equal(wrongAction.code, "delegation_action_not_granted");
});

test("exact linked user gate decision scope does not invent Agent authority", () => {
  const scope = {kind: "direction", granularity: "action", scope_key: "release"};
  const gate = {
    todo_id: "todo_gate",
    status: "open",
    role: "user",
    task_class: "user_gate",
    claimed_by: null,
    excluded_agents: [],
    bound_agent: null,
    blocks_agent: "agent-a",
    decision_scope: scope,
    required_decision_scopes: [],
    unblocks_todo_id: "todo_target",
  };
  const target = {
    ...request().todo as object,
    todo_id: "todo_target",
    required_decision_scopes: [scope],
  };
  const accepted = evaluateCoordinationTodoTerminalDecision(request({
    todo: gate,
    decision_target: target,
    actor_agent_id: null,
    decision_outcome: "approve",
  }));
  assert.equal(accepted.outcome, "apply");
  assert.equal(accepted.authority_mode, "exact_user_gate_decision_scope_override");

  const mismatch = evaluateCoordinationTodoTerminalDecision(request({
    todo: gate,
    decision_target: { ...target, required_decision_scopes: [] },
    actor_agent_id: null,
    decision_outcome: "approve",
  }));
  assert.equal(mismatch.code, "actor_required");
});

test("hard-lease terminal transition releases the exact owned generation", () => {
  const lease = {
    present: true,
    active: true,
    status: "active",
    owner: "agent-a",
    idempotency_key: "lease-a",
    version: 3,
    lease_epoch: 7,
    write_scopes: ["docs/**"],
    acquire_ttl_seconds: 600,
  };
  const missingFence = evaluateCoordinationTodoTerminalDecision(request({
    handoff_mode: "hard_lease",
    lease,
  }));
  assert.equal(missingFence.code, "lease_fence_required");
  const stale = evaluateCoordinationTodoTerminalDecision(request({
    handoff_mode: "hard_lease",
    lease,
    lease_idempotency_key: "lease-a",
    lease_expected_version: 2,
  }));
  assert.equal(stale.outcome, "conflict");
  assert.equal(stale.code, "version_mismatch");
  const accepted = evaluateCoordinationTodoTerminalDecision(request({
    handoff_mode: "hard_lease",
    lease,
    lease_idempotency_key: "lease-a",
    lease_expected_version: 3,
  }));
  assert.equal(accepted.outcome, "apply");
  assert.equal(accepted.lease_fence, "required");
  assert.equal(accepted.next_lease?.status, "released");
  assert.equal(accepted.next_lease?.version, 3);
  assert.equal(accepted.next_lease?.lease_epoch, 7);
});

test("hard-lease deferred supersede is owner-scoped without reviving execution", () => {
  const deferred = {...request().todo as object, status: "deferred"};
  const base = {command: "supersede", authority_action: "supersede",
    handoff_mode: "hard_lease", todo: deferred};
  const resumed = evaluateCoordinationTodoTerminalDecision(request(base));
  assert.equal(resumed.outcome, "apply");
  assert.equal(resumed.next_todo_status, "done");
  assert.equal(resumed.next_lease, null);
  assert.equal(resumed.lease_fence, "not_required");
  const expired = evaluateCoordinationTodoTerminalDecision(request({...base,
    lease: {present: true, active: false, status: "active", owner: "agent-a",
      idempotency_key: "old-lease", version: 2, lease_epoch: 2, write_scopes: []},
  }));
  assert.equal(expired.outcome, "apply");
  assert.equal(expired.next_lease?.status, "released");
  for (const [overrides, code] of [
    [{actor_agent_id: "agent-b"}, "claim_owner_mismatch"],
    [{todo: {...deferred, excluded_agents: ["agent-a"]}}, "actor_excluded"],
    [{lease: {present: true, active: true, status: "active", owner: "agent-a",
      idempotency_key: "live-lease", version: 2, lease_epoch: 2, write_scopes: []}},
    "handoff_mode_lease_claim_divergence"],
  ] as const) {
    assert.equal(evaluateCoordinationTodoTerminalDecision(request({...base, ...overrides})).code, code);
  }
  assert.equal(evaluateCoordinationTodoTerminalDecision(request({...base,
    command: "complete", authority_action: "complete"})).code, "handoff_mode_requires_lease");
});

test("hard-lease divergence and terminal replay fail closed in the established order", () => {
  const divergent = evaluateCoordinationTodoTerminalDecision(request({
    handoff_mode: "hard_lease",
    lease: {
      present: true,
      active: true,
      status: "active",
      owner: "agent-b",
      idempotency_key: "lease-b",
      version: 1,
      lease_epoch: 1,
      write_scopes: [],
      acquire_ttl_seconds: 600,
    },
  }));
  assert.equal(divergent.code, "handoff_mode_lease_claim_divergence");

  const replayed = evaluateCoordinationTodoTerminalDecision(request({
    todo: { ...request().todo as object, status: "done" },
    handoff_mode: "hard_lease",
  }));
  assert.equal(replayed.outcome, "no_change");
  assert.equal(replayed.code, "terminal_replay");
  assert.equal(replayed.idempotent, true);
});
