import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import { canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import { executeCoordinationTodoUpdate } from "../../loopx/control_plane/coordination/todo_update.ts";
import {updateLocalCoordinationTodo} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";

test("Monitor observation is versioned before any provider access", async () => {
  for (const version of [0, 1, 2, 3]) {
    const result = await updateLocalCoordinationTodo({schema_version: `loopx_local_coordination_todo_update_request_v${version}`,
      monitor_observation: {generated_at: "2030-01-01T00:00:00Z", result_hash: "a", material_change: true}},
    {createStore: () => {throw new Error("must not open a store");}});
    assert.equal(result.status, "failed"); assert.match(String(result.reason), /requires request v4/);
  }
  const missing = await updateLocalCoordinationTodo({schema_version: "loopx_local_coordination_todo_update_request_v4"},
    {createStore: () => {throw new Error("must not open a store");}});
  assert.equal(missing.status, "failed"); assert.match(String(missing.reason), /requires its observation/);
});

test("planning transport is explicitly versioned before any provider access", async () => {
  const result = await updateLocalCoordinationTodo({
    schema_version: "loopx_local_coordination_todo_update_request_v0",
    patch: {text: "Must not partially commit"}, planning_intent: {status: "blocked"},
  }, {createStore: () => {throw new Error("must not open a store");}});
  assert.equal(result.status, "failed");
  assert.match(String(result.reason), /requires the v1/);
});

test("review admission and CAS cannot be downgraded to a partial old-wire edit", async () => {
  for (const schema_version of ["loopx_local_coordination_todo_update_request_v0",
    "loopx_local_coordination_todo_update_request_v1"]) {
    for (const added of [{authority_reason: "Reviewed"}, {lifecycle_grants: []},
      {expected_provider_revision: "basis"}, {expected_registry_sha256: "a".repeat(64)}]) {
      const result = await updateLocalCoordinationTodo({schema_version,
        patch: {text: "No partial write"}, ...added},
      {createStore: () => {throw new Error("must not open a store");}});
      assert.equal(result.status, "failed");
      assert.match(String(result.reason), /require request v2/);
    }
  }
});

test("delegated reassign cannot smuggle a copy edit or override exclusion/binding", async () => {
  const {store, request} = await seeded();
  const transfer = {...request, actor_agent_id: "agent-b", patch: {}, clear_fields: [],
    planning_intent: {claimed_by: "agent-b"}, authority_reason: "Reviewed reassignment",
    lifecycle_grants: [{agent_id: "agent-b", actions: ["reassign"], requires_reason: true}]};
  const before = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, {...transfer, patch: {note: "Bundled edit"}})).reason_code,
    "delegation_action_not_granted");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await executeCoordinationTodoUpdate(store, transfer)).status, "applied");
  for (const [facts, code] of [[{excluded_agents: ["agent-b"]}, "actor_excluded"],
    [{bound_agent: "agent-a"}, "bound_agent_mismatch"]] as const) {
    const scoped = await seeded(facts);
    assert.equal((await executeCoordinationTodoUpdate(scoped.store, transfer)).reason_code, code);
    assert.equal((await scoped.store.readReceipt(transfer.operation_id)).status, "missing");
  }
});

test("native planning edit commits nonterminal state and clears its wait atomically", async () => {
  const {store, request} = await seeded({task_class: "advancement_task"});
  const edit = {...request, patch: {text: "Old text"}, clear_fields: [], planning_intent: {
    status: "deferred", resume_when: "pr_merged:#123", reason: "Waiting for upstream",
  }};
  const before = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "applied");
  const deferred = await store.loadAuthority();
  assert.equal(deferred.status, "loaded");
  if (deferred.status !== "loaded") return;
  const record = (deferred.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(record.status, "deferred");
  assert.equal(record.done, true);
  assert.equal(record.resume_when, "pr_merged:#123");
  assert.equal(record.claimed_by, "agent-a");
  assert.equal(record.note, "old note");
  assert.equal(Object.hasOwn(record, "last_actor_agent_id"), false);
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "replayed");
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit,
    planning_intent: {...edit.planning_intent, reason: "Different intent"}})).reason_code,
    "coordination_operation_identity_mismatch");
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, operation_id: "resume-a",
    planning_intent: {status: "open", clear_resume_when: true}})).status, "applied");
  const resumed = await store.loadAuthority();
  assert.equal(resumed.status, "loaded");
  if (resumed.status !== "loaded") return;
  const next = (resumed.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(next.status, "open");
  assert.equal(next.done, false);
  assert.equal(next.resume_when, undefined);
  assert.equal(next.resume_monitor_generation, undefined);
});

test("planning intent cannot smuggle terminal, decision or observation writes", async () => {
  const {store, request} = await seeded({task_class: "advancement_task"});
  for (const planning_intent of [
    {status: "done"}, {decision_outcome: "approve"},
    {decision_scope: {kind: "direction", granularity: "goal", scope_key: "release"}},
    {global_gate: true}, {monitor_metadata: {material_change: "true"}},
    {completion_metadata_updates_override: {completion_continuation: "no_followup"}},
    {status: "deferred"}, {successor_todo_ids: "todo_other"},
  ]) {
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoUpdate(store, {...request, planning_intent});
    assert.equal(result.status, "failed", JSON.stringify(planning_intent));
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  }
});

test("owner can transfer and clear a claim; retry cannot restore an old owner", async () => {
  const {store, request} = await seeded({task_class: "advancement_task"});
  const transfer = {...request, patch: {}, clear_fields: [], planning_intent: {claimed_by: "Agent B"}};
  const before = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, {...transfer, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await executeCoordinationTodoUpdate(store, transfer)).status, "applied");
  const transferred = await store.loadAuthority();
  assert.equal(transferred.status, "loaded");
  if (transferred.status !== "loaded") return;
  const row = (transferred.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(row.claimed_by, "agent-b");
  assert.equal(row.last_actor_agent_id, "agent-a");
  assert.equal((await executeCoordinationTodoUpdate(store, {...request, operation_id: "old-owner-edit"})).reason_code,
    "update_owner_mismatch");
  assert.equal((await executeCoordinationTodoUpdate(store, {...transfer, operation_id: "clear-owner",
    actor_agent_id: "agent-b", planning_intent: {clear_claim: true}})).status, "applied");
  const cleared = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, {...transfer,
    planning_intent: {claimed_by: "agent-b"}})).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), cleared);
});

test("exclusion edits are atomic, normalized, and cannot exempt their excluded author", async () => {
  const {store, request} = await seeded({task_class: "advancement_task"});
  const edit = {...request, patch: {}, clear_fields: [], planning_intent: {excluded_agents: ["Agent B", "agent-b"]}};
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "applied");
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, operation_id: "excluded-author",
    actor_agent_id: "agent-b", planning_intent: {excluded_agents: []}})).reason_code, "actor_excluded");
  const before = await store.loadAuthority();
  for (const planning_intent of [{claimed_by: "unregistered"}, {excluded_agents: ["agent-b", "unregistered"]},
    {claimed_by: "agent-b", clear_claim: true}, {excluded_agents: "agent-b"}]) {
    const rejected = await executeCoordinationTodoUpdate(store, {...request, operation_id: "invalid-owner", planning_intent});
    assert.equal(rejected.status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt("invalid-owner")).status, "missing");
  }
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, operation_id: "clear-exclusions",
    planning_intent: {excluded_agents: []}})).status, "applied");
});

function todo(overrides: Record<string, unknown> = {}) {
  return {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Old text", archive_state: "active",
    claimed_by: "agent-a", note: "old note", ...overrides};
}

async function seeded(overrides: Record<string, unknown> = {}) {
  const root = await mkdtemp(join(tmpdir(), "loopx-todo-update-"));
  const store = new FileAuthorityStore(root, "goal-a");
  const records = [todo(overrides)];
  if (records[0]!.claimed_by === null) Reflect.deleteProperty(records[0]!, "claimed_by");
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: "goal-a", todos: records, leases: [],
      todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
        records_sha256: canonicalAuthoritySha256(records),
        contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const request = {goal_id: "goal-a", todo_id: "todo_a", expected_role: "agent",
    actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
    operation_id: "update-a", patch: {text: "New text"}, clear_fields: ["note"],
    dry_run: false, now: new Date("2026-09-05T23:00:00Z")};
  return {store, request};
}

test("provider-first update commits complete record and replays by intent", async () => {
  const {store, request} = await seeded();
  const preview = await executeCoordinationTodoUpdate(store, {...request, dry_run: true});
  assert.equal(preview.status, "planned");
  const applied = await executeCoordinationTodoUpdate(store, request);
  assert.equal(applied.status, "applied");
  assert.equal((applied.original_receipt as Record<string, unknown>).request_sha256,
    canonicalAuthoritySha256({goal_id: request.goal_id, todo_id: request.todo_id,
      expected_role: request.expected_role, actor_agent_id: request.actor_agent_id,
      patch: request.patch, clear_fields: request.clear_fields, dry_run: request.dry_run}));
  assert.equal((await executeCoordinationTodoUpdate(store, {...request, planning_intent: {}})).status, "replayed");
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  const updated = (head.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(updated.text, "New text");
  assert.equal(updated.note, undefined);
  assert.equal(updated.claimed_by, "agent-a");
  assert.equal(updated.last_actor_agent_id, "agent-a");
  assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
  assert.equal((await executeCoordinationTodoUpdate(store, {...request,
    patch: {text: "Different"}})).reason_code, "coordination_operation_identity_mismatch");
});

test("planning no-op consumes its identity but never claims unclaimed work", async () => {
  const {store, request} = await seeded({claimed_by: null, task_class: "advancement_task", reason: "Same"});
  const edit = {...request, patch: {}, clear_fields: [], planning_intent: {reason: "Same"}};
  const before = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "no_change");
  const after = await store.loadAuthority();
  assert.equal(before.status, "loaded");
  assert.equal(after.status, "loaded");
  if (before.status !== "loaded" || after.status !== "loaded") return;
  assert.deepEqual(after.head, before.head);
  assert.equal((await store.readReceipt(edit.operation_id)).status, "found");
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, operation_id: "later",
    planning_intent: {reason: "Later"}})).status, "applied");
  const latest = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), latest);
});

test("unclaimed text edits are claim-neutral, including preview and replay", async () => {
  const {store, request} = await seeded({claimed_by: null});
  const before = await store.loadAuthority();
  for (const actor_agent_id of [null, "unknown"]) {
    assert.equal((await executeCoordinationTodoUpdate(store, {...request, actor_agent_id})).reason_code,
      "actor_not_registered");
  }
  assert.equal((await executeCoordinationTodoUpdate(store, {...request, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
  assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  const updated = (after.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(updated.text, "New text");
  assert.equal(updated.note, undefined);
  assert.equal(Object.hasOwn(updated, "claimed_by"), false);
  assert.equal(updated.last_actor_agent_id, "agent-a");
});

test("unclaimed edits preserve actor exclusion and binding fences", async () => {
  for (const [overrides, reason] of [
    [{excluded_agents: ["agent-a"]}, "actor_excluded"],
    [{bound_agent: "agent-b"}, "bound_agent_mismatch"],
  ] as const) {
    const {store, request} = await seeded({claimed_by: null, ...overrides});
    const before = await store.loadAuthority();
    assert.equal((await executeCoordinationTodoUpdate(store, request)).reason_code, reason);
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  }
});

test("single-agent compatibility preserves ownership, exclusion, and binding fences", async () => {
  for (const [label, overrides, reason] of [
    ["claimed", {claimed_by: "agent-b"}, "update_owner_mismatch"],
    ["excluded", {claimed_by: null, excluded_agents: ["agent-a"]}, "actor_excluded"],
    ["bound", {claimed_by: null, bound_agent: "agent-b"}, "bound_agent_mismatch"],
  ] as const) {
    const {store, request} = await seeded(overrides);
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoUpdate(store, {
      ...request,
      operation_id: `single-agent-${label}`,
      registered_agents: ["agent-a"],
      actor_agent_id: "agent-a",
    });
    assert.equal(result.reason_code, reason, JSON.stringify(result));
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(`single-agent-${label}`)).status, "missing");
  }
});

test("single-agent actorless compatibility rejects excluded work", async () => {
  const {store, request} = await seeded({claimed_by: null, excluded_agents: ["agent-a"]});
  const before = await store.loadAuthority();
  const result = await executeCoordinationTodoUpdate(store, {
    ...request,
    operation_id: "single-agent-actorless-excluded",
    registered_agents: ["agent-a"],
    actor_agent_id: null,
  });
  assert.equal(result.reason_code, "actor_required", JSON.stringify(result));
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await store.readReceipt("single-agent-actorless-excluded")).status, "missing");
});

test("provider-first update rejects authority and lifecycle escalation", async () => {
  const {store, request} = await seeded();
  assert.equal((await executeCoordinationTodoUpdate(store, {...request,
    actor_agent_id: "agent-b"})).reason_code, "update_owner_mismatch");
  assert.equal((await executeCoordinationTodoUpdate(store, {...request,
    actor_agent_id: "unknown"})).reason_code, "actor_not_registered");
  for (const patch of [{status: "done"}, {claimed_by: "agent-b"}, {archive_state: "archive"},
    {excluded_agents: ["agent-a"]}, {required_capabilities: ["network"]},
    {required_decision_scopes: ["release"]}, {continuation_policy: "no_followup"},
    {task_repository: "git:example.invalid/repo"}, {successor_todo_ids: ["todo_b"]}]) {
    const result = await executeCoordinationTodoUpdate(store, {...request, patch});
    assert.equal(result.reason_code, "invalid_coordination_todo_update");
  }
  for (const clear_fields of [["excluded_agents"], ["required_capabilities"],
    ["required_decision_scopes"], ["continuation_policy"], ["task_repository"],
    ["successor_todo_ids"]]) {
    const result = await executeCoordinationTodoUpdate(store, {...request,
      patch: {}, clear_fields});
    assert.equal(result.reason_code, "invalid_coordination_todo_update");
  }
});

for (const claimed_by of ["agent-a", null]) {
test(`provider-first update fails closed without a hard-lease execution proof (${claimed_by ?? "unclaimed"})`, async () => {
  const {store, request} = await seeded({claimed_by});
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  await store.commitAuthority({operation_id: "seed-lease",
    expected_provider_revision: head.provider_revision, events: [], receipts: [],
    next_projection: {...head.head, handoff_mode: "hard_lease", leases: [{
      todo_id: "todo_a", owner: "agent-a", status: "active",
      expires_at: "2026-09-06T00:00:00Z",
      idempotency_key: "execution-a", version: 1, lease_epoch: 1,
    }]}});
  const result = await executeCoordinationTodoUpdate(store, request);
  assert.equal(result.reason_code, "lease_fence_required");
  assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
});
}

test("provider-first update records no-change identity without state mutation", async () => {
  const {store, request} = await seeded();
  const before = await store.loadAuthority();
  const noChangeRequest = {...request,
    patch: {text: "Old text"}, clear_fields: [], operation_id: "no-change"};
  const result = await executeCoordinationTodoUpdate(store, noChangeRequest);
  assert.equal(result.status, "no_change");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (before.status !== "loaded" || after.status !== "loaded") return;
  assert.deepEqual(after.head, before.head);
  assert.notEqual(after.provider_revision, before.provider_revision);
  // A consumed no-change identity must not overwrite a later edit on retry.
  assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
  const later = await store.loadAuthority();
  const replay = await executeCoordinationTodoUpdate(store, noChangeRequest);
  assert.equal(replay.status, "replayed");
  assert.equal(replay.changed, false);
  assert.deepEqual(await store.loadAuthority(), later);
  // Identical parameters under a new attempt are new work, not stale replay.
  assert.equal((await executeCoordinationTodoUpdate(store, {...noChangeRequest,
    operation_id: "independent-reset"})).status, "applied");
});

for (const [label, leaseChange, todoChange, reason] of [
  ["released", {status: "released"}, {}, "handoff_mode_requires_lease"],
  ["expired", {expires_at: "2026-01-01T00:00:00Z"}, {}, "handoff_mode_requires_lease"],
  ["malformed expiry", {expires_at: "invalid"}, {}, "invalid_coordination_projection"],
  ["malformed epoch", {lease_epoch: -1}, {}, "invalid_coordination_projection"],
  ["unclaimed", {}, {claimed_by: null}, "update_owner_mismatch"],
] as const) {
  test(`leased update rejects ${label} without writing`, async () => {
    const {store, request} = await seeded(todoChange);
    const head = await store.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status !== "loaded") return;
    await store.commitAuthority({operation_id: "lease-invalid-case",
      expected_provider_revision: head.provider_revision, events: [], receipts: [],
      next_projection: {...head.head, handoff_mode: "hard_lease", leases: [{
        todo_id: "todo_a", owner: "agent-a", status: "active", version: 2, lease_epoch: 2,
        idempotency_key: "execution-a", expires_at: "2026-09-06T00:00:00Z", ...leaseChange,
      }]}});
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoUpdate(store, {...request,
      lease_idempotency_key: "execution-a", lease_expected_version: 2});
    assert.equal(result.reason_code, reason);
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  });
}
