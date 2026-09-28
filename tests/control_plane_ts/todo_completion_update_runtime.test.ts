import assert from "node:assert/strict";
import {resolve} from "node:path";
import test from "node:test";
import {
  LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
  terminalLifecycleLocalCoordinationTodo,
  updateLocalCoordinationTodo,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";

for (const version of [0, 1, 2]) {
  test(`update v${version} cannot silently ignore completion payload`, async () => {
    let opened = false;
    const result = await updateLocalCoordinationTodo({schema_version: `loopx_local_coordination_todo_update_request_v${version}`,
      completion: {}}, {createStore: () => {opened = true; throw new Error("must not open provider");}});
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /completion payload requires request v3/);
    assert.equal(opened, false);
  });
}
test("completion v3 requires an explicit effect envelope", async () => {
  const result = await updateLocalCoordinationTodo({schema_version: "loopx_local_coordination_todo_update_request_v3"});
  assert.equal(result.status, "failed");
  assert.match(String(result.reason), /requires its completion payload/);
});
test("malformed completion facts fail before any provider read", async () => {
  const store = new Proxy({} as AuthorityStore, {get: () => {throw new Error("invalid intent reached provider");}});
  for (const completion of [{validation_receipt: []}, {unknown_field: true}, {source_provider_revision: 1}]) {
    const result = await executeCoordinationTodoUpdate(store, {goal_id: "g", todo_id: "todo_a", expected_role: "user",
      actor_agent_id: "agent-a", registered_agents: ["agent-a"], operation_id: "op", patch: {}, clear_fields: [],
      planning_intent: {status: "done"}, completion, dry_run: false, now: new Date()});
    assert.equal(result.reason_code, "invalid_coordination_todo_update");
  }
});

function monitorCycleTerminalRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
    runtime_root: "/unused",
    registry_source: {
      path: resolve("unused-registry.json"),
      sha256: "0".repeat(64),
    },
    goal_id: "g",
    todo_id: "todo_a",
    role: "agent",
    command: "complete",
    actor_agent_id: "agent-a",
    registered_agents: ["agent-a"],
    lifecycle_grants: [],
    authority_reason: null,
    decision_outcome: null,
    operation_identity: {kind: "current_monitor_cycle" as const},
    lease_idempotency_key: null,
    lease_expected_version: null,
    allow_user_gate_auto_acquire: false,
    requested_no_followup: true,
    requested_completion_turn_key: null,
    requested_completion_identity_source: null,
    linked_successor_todo_ids: [],
    successor_intents: [],
    note: null,
    evidence: null,
    reason: null,
    clear_claim: false,
    validation_declaration: null,
    validation_receipt: null,
    completion_policy_request: null,
    dry_run: false,
    observed_at: "2026-09-23T00:00:00Z",
    ...overrides,
  };
}

test("terminal identity rejects ambiguous or inapplicable intent before opening a provider", async () => {
  for (const [overrides, reason] of [
    [{operation_id: "caller-selected"}, /use operation_identity/],
    [{operation_identity: null}, /terminal operation identity/],
    [{operation_identity: undefined}, /terminal operation identity/],
    [{requested_completion_turn_key: ""}, /must be a non-empty string/],
    [{requested_completion_turn_key: "  "}, /must be a non-empty string/],
    [{operation_identity: {kind: "explicit"}}, /operation id/],
    [{operation_identity: {kind: "explicit", operation_id: ""}}, /operation id/],
    [{operation_identity: {kind: "unknown"}}, /invalid terminal operation identity/],
    [{operation_identity: {kind: "current_monitor_cycle", operation_id: "ambiguous"}}, /invalid terminal operation identity/],
    [{operation_identity: {kind: "explicit", operation_id: "op", extra: true}}, /invalid terminal operation identity/],
    [{command: "supersede"}, /requires an unkeyed completion/],
    [{requested_completion_turn_key: "explicit-turn"}, /requires an unkeyed completion/],
    [{operation_identity: {kind: "completion_turn"}}, /requires a keyed turn_settlement completion/],
    [{operation_identity: {kind: "completion_turn"}, requested_completion_turn_key: "turn"}, /requires a keyed turn_settlement completion/],
    [{operation_identity: {kind: "completion_turn"}, command: "supersede",
      requested_completion_turn_key: "turn", requested_completion_identity_source: "turn_settlement"}, /requires a keyed turn_settlement completion/],
    [{operation_identity: {kind: "completion_turn", operation_id: "ambiguous"},
      requested_completion_turn_key: "turn", requested_completion_identity_source: "turn_settlement"}, /invalid terminal operation identity/],
  ] as const) {
    let opened = false;
    const result = await terminalLifecycleLocalCoordinationTodo(monitorCycleTerminalRequest(overrides), {
      createStore: () => {opened = true; throw new Error("invalid intent reached provider");},
    });
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), reason);
    assert.equal(opened, false);
  }
});

for (const version of [0, 1, 2]) {
  test(`retired terminal v${version} is rejected before opening a provider`, async () => {
    let opened = false;
    const result = await terminalLifecycleLocalCoordinationTodo(monitorCycleTerminalRequest({
      schema_version: `loopx_local_coordination_todo_terminal_lifecycle_request_v${version}`,
    }), {createStore: () => {opened = true; throw new Error("must not open provider");}});
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /schema mismatch; regenerate with the current runtime/);
    assert.equal(opened, false);
  });
}
