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
import {configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
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

test("completion validation revision is versioned before provider access", async () => {
  const oldWire = await updateLocalCoordinationTodo({
    schema_version: "loopx_local_coordination_todo_update_request_v2",
    completion_validation_revision: {},
  }, {createStore: () => {throw new Error("must not open a store");}});
  assert.equal(oldWire.status, "failed");
  assert.match(String(oldWire.reason), /requires request v5/);
  const missing = await updateLocalCoordinationTodo({
    schema_version: "loopx_local_coordination_todo_update_request_v5",
  }, {createStore: () => {throw new Error("must not open a store");}});
  assert.equal(missing.status, "failed");
  assert.match(String(missing.reason), /requires its revision payload/);
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

test("delegated controller reopens a promoted legacy claim before its first lease", async () => {
  const {store, request} = await seeded({status: "blocked", done: false, claimed_by: "agent-a"});
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  await store.commitAuthority({operation_id: "promote-without-inventing-lease",
    expected_provider_revision: head.provider_revision, events: [], receipts: [],
    next_projection: {...head.head, handoff_mode: "hard_lease", leases: []}});
  const edit = {...request, operation_id: "reviewed-reopen", actor_agent_id: "agent-b",
    patch: {note: "Promotion resolved the stale blocker"}, clear_fields: [],
    planning_intent: {status: "open", reason: "Canonical authority is promoted"},
    authority_reason: "Reviewed controller recovery after promotion",
    lifecycle_grants: [{agent_id: "agent-b", actions: ["update"], requires_reason: true}]};
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "applied");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  const updated = (after.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(updated.status, "open");
  assert.equal(updated.claimed_by, "agent-a");
  assert.equal(updated.note, "Promotion resolved the stale blocker");
  assert.deepEqual(after.head.leases, []);
  await store.commitAuthority({operation_id: "worker-acquired-first-lease",
    expected_provider_revision: after.provider_revision, events: [], receipts: [],
    next_projection: {...after.head, leases: [{todo_id: "todo_a", owner: "agent-a",
      status: "active", expires_at: "2026-09-06T00:00:00Z", idempotency_key: "execution-a",
      version: 1, lease_epoch: 1, write_scopes: []}]}});
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit,
    operation_id: "controller-cannot-cross-live-lease",
    patch: {note: "Must not cross active execution"}})).reason_code, "lease_fence_required");
});

test("claimed deferred Todo can reopen after its time gate under hard lease", async () => {
  const {store, request} = await seeded({status: "deferred", done: true,
    task_class: "advancement_task", resume_when: "resume_at:2026-09-05T22:00:00Z"});
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  await store.commitAuthority({operation_id: "enable-hard-lease",
    expected_provider_revision: head.provider_revision, events: [], receipts: [],
    next_projection: {...head.head, handoff_mode: "hard_lease"}});
  const resume = {...request, operation_id: "resume-deferred", patch: {}, clear_fields: [],
    planning_intent: {status: "open", clear_resume_when: true}};
  const result = await executeCoordinationTodoUpdate(store, resume);
  assert.equal(result.status, "applied", JSON.stringify(result));
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  const todo = (after.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(todo.status, "open");
  assert.equal(todo.resume_when, undefined);
  assert.equal(todo.claimed_by, "agent-a");
  assert.deepEqual(after.head.leases, []);
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

test("first validator binding requires explicit absence, current CAS and active lease", async () => {
  const {store, request} = await seeded();
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") return;
  await store.commitAuthority({operation_id: "lease-before-binding",
    expected_provider_revision: loaded.provider_revision, events: [], receipts: [],
    next_projection: {...loaded.head, handoff_mode: "hard_lease", leases: [{
      todo_id: request.todo_id, owner: "agent-a", status: "active",
      expires_at: "2026-09-06T00:00:00Z", idempotency_key: "binding-execution",
      version: 1, lease_epoch: 1, write_scopes: [],
    }]}});
  const before = await store.loadAuthority();
  assert.equal(before.status, "loaded");
  if (before.status !== "loaded") return;
  const declaration = {validation_command: null, validation_command_argv: ["false"],
    validation_label: "independent check", validation_timeout_seconds: 20};
  const edit = {...request, patch: {}, clear_fields: [], operation_id: "bind-first",
    expected_provider_revision: before.provider_revision,
    lease_idempotency_key: "binding-execution", lease_expected_version: 1,
    completion_validation_revision: {schema_version: "loopx_todo_completion_validation_revision_v1",
      expected_declaration_sha256: null, declaration}} as const;
  for (const overrides of [{actor_agent_id: "agent-b"}, {lease_expected_version: 0},
    {expected_provider_revision: loaded.provider_revision}]) {
    assert.equal((await executeCoordinationTodoUpdate(store, {...edit, ...overrides})).status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(edit.operation_id)).status, "missing");
  }
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "applied");
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "replayed");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  const bound = (after.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(bound.completion_validation_required, true);
  assert.equal(bound.completion_validation_sha256, canonicalAuthoritySha256(declaration));
  assert.equal(bound.status, "open", "binding must not complete or execute work");
  assert.deepEqual(after.head.leases, before.head.leases);
  const receipt = (bound.completion_validation_revision_history as Record<string, unknown>[])[0]!;
  assert.equal(receipt.previous_declaration_sha256, null);
  assert.deepEqual(receipt.previous_validation_authority, {});
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit,
    completion_validation_revision: {...edit.completion_validation_revision,
      declaration: {...declaration, validation_command_argv: ["true"]}}})).reason_code,
  "coordination_operation_identity_mismatch");
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, operation_id: "second-first-bind",
    expected_provider_revision: after.provider_revision})).status, "failed");
});

test("first binding rejects malformed absence and incompatible history without partial writes", async () => {
  const declaration = {validation_command: null, validation_command_argv: ["true"],
    validation_label: "independent check", validation_timeout_seconds: 5};
  for (const metadata of [
    {completion_validation_required: true}, {completion_validation_sha256: null},
    {completion_validation_revision: 1}, {completion_validation_revision_history: [{}]},
    {status: "done", done: true}, {archive_state: "archived"},
  ]) {
    const {store, request} = await seeded(metadata);
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoUpdate(store, {...request, patch: {}, clear_fields: [],
      completion_validation_revision: {schema_version: "loopx_todo_completion_validation_revision_v1",
        expected_declaration_sha256: null, declaration}});
    assert.equal(result.status, "failed", JSON.stringify(result));
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  }
  for (const revision of [
    {schema_version: "loopx_todo_completion_validation_revision_v0", expected_declaration_sha256: null, declaration},
    {schema_version: "loopx_todo_completion_validation_revision_v1", expected_declaration_sha256: "a".repeat(64), declaration},
    {schema_version: "loopx_todo_completion_validation_revision_v1", declaration},
    {schema_version: ["loopx_todo_completion_validation_revision_v0"], expected_declaration_sha256: "a".repeat(64), declaration},
  ]) {
    const result = await updateLocalCoordinationTodo({schema_version: "loopx_local_coordination_todo_update_request_v5",
      completion_validation_revision: revision},
    {createStore: () => {throw new Error("invalid absence must not open a provider");}});
    assert.equal(result.status, "failed");
  }
});

test("open Todo revises its validator with CAS, audit history, and idempotent replay", async () => {
  const original = {
    validation_command: null,
    validation_command_argv: ["python", "-m", "pytest", "-q", "tests/old.py"],
    validation_label: "focused tests",
    validation_timeout_seconds: 20,
  };
  const replacement = {...original,
    validation_command_argv: ["python", "-m", "pytest", "-q", "tests/new.py"]};
  const {store, request} = await seeded({
    completion_validation_required: true,
    completion_validation_sha256: canonicalAuthoritySha256(original),
    completion_validation_revision: 0,
    completion_validation_revision_history: [],
  });
  const before = await store.loadAuthority();
  assert.equal(before.status, "loaded");
  if (before.status !== "loaded") return;
  const revision = {
    schema_version: "loopx_todo_completion_validation_revision_v0",
    expected_declaration_sha256: canonicalAuthoritySha256(original),
    declaration: replacement,
  } as const;
  const edit = {...request, patch: {}, clear_fields: [],
    expected_provider_revision: before.provider_revision,
    completion_validation_revision: revision};
  const preview = await executeCoordinationTodoUpdate(store, {...edit, dry_run: true});
  assert.equal(preview.status, "planned");
  assert.equal((preview.completion_validation_revision as Record<string, unknown>).revision, 1);
  assert.equal((await executeCoordinationTodoUpdate(store, edit)).status, "applied");
  const replay = await executeCoordinationTodoUpdate(store, edit);
  assert.equal(replay.status, "replayed");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  const updated = (after.head.todos as Record<string, unknown>[])[0]!;
  assert.equal(updated.completion_validation_sha256, canonicalAuthoritySha256(replacement));
  assert.equal(updated.completion_validation_revision, 1);
  const history = updated.completion_validation_revision_history as Record<string, unknown>[];
  assert.equal(history.length, 1);
  assert.equal(history[0]!.previous_declaration_sha256, canonicalAuthoritySha256(original));
  assert.equal(history[0]!.declaration_sha256, canonicalAuthoritySha256(replacement));
  assert.equal(history[0]!.actor_agent_id, "agent-a");
  assert.equal((await executeCoordinationTodoUpdate(store, {...edit, operation_id: "stale",
    completion_validation_revision: {...revision,
      declaration: {...replacement, validation_label: "different"}}})).reason_code,
    "provider_revision_mismatch");
});

test("terminal or digest-stale Todo rejects validator revision without a receipt", async () => {
  const declaration = {validation_command: null, validation_command_argv: ["true"],
    validation_label: null, validation_timeout_seconds: null};
  for (const overrides of [
    {status: "done", done: true},
    {completion_validation_sha256: "f".repeat(64)},
  ]) {
    const {store, request} = await seeded({completion_validation_required: true,
      completion_validation_sha256: canonicalAuthoritySha256(declaration), ...overrides});
    const edit = {...request, patch: {}, clear_fields: [], completion_validation_revision: {
      schema_version: "loopx_todo_completion_validation_revision_v0",
      expected_declaration_sha256: canonicalAuthoritySha256(declaration),
      declaration: {...declaration, validation_command_argv: ["false"]},
    }};
    const result = await executeCoordinationTodoUpdate(store, edit);
    assert.equal(result.status, "failed");
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  }
});

test("validator revision rejects malformed declarations before provider mutation", async () => {
  const original = {
    validation_command: null,
    validation_command_argv: ["true"],
    validation_label: null,
    validation_timeout_seconds: null,
  };
  const malformed = [
    {unexpected: "accepted"},
    {...original, unexpected: true},
    {...original, validation_command: "true"},
    {...original, validation_command_argv: '["true"]'},
    {...original, validation_timeout_seconds: "20"},
    {...original, validation_label: ""},
    {...original, validation_command: " true ", validation_command_argv: null},
    {
      validation_command: null,
      validation_command_argv: ["true"],
      validation_timeout_seconds: null,
    },
    {...original, validation_command_argv: []},
    {...original, validation_command_argv: ["true", ""]},
    {...original, validation_command_argv: 1},
    {...original, validation_timeout_seconds: 30},
    {...original, validation_timeout_seconds: true},
    {...original, validation_label: 1},
  ];
  for (const [index, declaration] of malformed.entries()) {
    const {store, request} = await seeded({
      completion_validation_required: true,
      completion_validation_sha256: canonicalAuthoritySha256(original),
      completion_validation_revision: 0,
      completion_validation_revision_history: [],
    });
    const before = await store.loadAuthority();
    assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    const operationId = `reject-malformed-validator-${index}`;
    const result = await executeCoordinationTodoUpdate(store, {
      ...request,
      operation_id: operationId,
      expected_provider_revision: before.provider_revision,
      patch: {},
      clear_fields: [],
      completion_validation_revision: {
        schema_version: "loopx_todo_completion_validation_revision_v0",
        expected_declaration_sha256: canonicalAuthoritySha256(original),
        declaration,
      },
    });
    assert.equal(result.status, "failed", JSON.stringify(declaration));
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(operationId)).status, "missing");
  }
});

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


test("schema-less historical canonical leases retain active execution semantics without mutation", async () => {
  const {canonicalTaskLease} = await import("../../loopx/control_plane/coordination/task_lease_state.ts");
  const {leaseIsActive} = await import("../../loopx/control_plane/work_items/task_lease_acquire.ts");
  const original = {todo_id: "todo_a", owner: "agent-a", idempotency_key: "execution-a",
    status: "active", version: 1, lease_epoch: 1, expires_at: "2031-01-01T00:00:00Z"};
  const normalized = canonicalTaskLease(original, "goal-a", "todo_a");
  assert.equal(normalized.schema_version, "task_lease_v0");
  assert.equal(leaseIsActive(normalized, new Date("2030-01-01T00:00:00Z")), true);
  assert.equal(Object.hasOwn(original, "schema_version"), false);
  assert.throws(() => canonicalTaskLease({...original, schema_version: "unknown"}, "goal-a", "todo_a"), /schema/);
});


test("retained lease diagnostics distinguish recovery without granting execution", async () => {
  for (const mode of ["legacy", "soft_claim", "hard_lease"]) {
    for (const state of ["released", "expired", "active"]) {
      for (const owner of ["agent-a", "agent-b"]) {
        const {store, request} = await seeded();
        const head = await store.loadAuthority();
        assert.equal(head.status, "loaded");
        if (head.status !== "loaded") continue;
        await store.commitAuthority({operation_id: "lease-fixture", expected_provider_revision: head.provider_revision,
          events: [], receipts: [], next_projection: {...head.head, handoff_mode: mode,
            leases: [{todo_id: "todo_a", owner, idempotency_key: "private-execution-key",
              version: 4, lease_epoch: 2, status: state === "released" ? "released" : "active",
              expires_at: state === "expired" ? "2026-09-04T00:00:00Z" : "2026-09-06T00:00:00Z", write_scopes: []}]}});
        const before = await store.loadAuthority();
        const result = await executeCoordinationTodoUpdate(store, {...request, dry_run: true});
        assert.equal(result.status, "failed");
        assert.equal(result.handoff_mode, mode);
        const recovery = result.recovery as Record<string, unknown>;
        assert.equal(recovery.lease_state, state);
        assert.equal(recovery.owner_relation, owner === "agent-a" ? "same_owner" : "foreign_owner");
        assert.equal(recovery.action, state === "active" ? owner === "agent-a" ? "inspect_current_proof" : "reconcile_lease_owner"
          : mode === "soft_claim" ? "resolve_acquire_rejection" : "acquire_fresh_lease");
        assert.equal(JSON.stringify(result).includes("private-execution-key"), false);
        assert.deepEqual(await store.loadAuthority(), before);
        assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
      }
    }
  }
});


test("recovery preserves stale-proof rejection and never offers foreign proof", async () => {
  for (const [proof, code] of [
    [{lease_idempotency_key: "stale-key", lease_expected_version: 4}, "lease_cas_mismatch"],
    [{lease_idempotency_key: "current-key", lease_expected_version: 3}, "version_mismatch"],
    [{lease_idempotency_key: "current-key"}, "version_required"],
  ] as const) {
    const {store, request} = await seeded();
    const head = await store.loadAuthority();
    if (head.status !== "loaded") assert.fail("missing fixture");
    await store.commitAuthority({operation_id: "lease", expected_provider_revision: head.provider_revision,
      events: [], receipts: [], next_projection: {...head.head, handoff_mode: "legacy",
        leases: [{todo_id: "todo_a", owner: "agent-a", idempotency_key: "current-key",
          version: 4, lease_epoch: 1, status: "active", expires_at: "2026-09-06T00:00:00Z", write_scopes: []}]}});
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoUpdate(store, {...request, ...proof});
    assert.equal(result.reason_code, code);
    assert.equal((result.recovery as Record<string, unknown>).action, "inspect_current_proof");
    assert.equal(JSON.stringify(result).includes("current-key"), false);
    assert.deepEqual(await store.loadAuthority(), before);
  }
  const {store, request} = await seeded();
  const head = await store.loadAuthority();
  if (head.status !== "loaded") assert.fail("missing fixture");
  await store.commitAuthority({operation_id: "foreign-lease", expected_provider_revision: head.provider_revision,
    events: [], receipts: [], next_projection: {...head.head, handoff_mode: "legacy",
      leases: [{todo_id: "todo_a", owner: "agent-b", idempotency_key: "foreign-key",
        version: 3, lease_epoch: 1, status: "active", expires_at: "2026-09-06T00:00:00Z", write_scopes: []}]}});
  const before = await store.loadAuthority();
  const result = await executeCoordinationTodoUpdate(store, request);
  const recovery = result.recovery as Record<string, unknown>;
  assert.equal(recovery.owner_relation, "foreign_owner");
  assert.equal(recovery.action, "reconcile_lease_owner");
  assert.equal(recovery.acquire, undefined);
  assert.equal(recovery.retry, undefined);
  assert.equal(JSON.stringify(result).includes("foreign-key"), false);
  assert.deepEqual(await store.loadAuthority(), before);
});

test("inactive history does not invent an executable lease acquisition", async () => {
  for (const [overrides, intent, mode, expected] of [
    [{status: "blocked"}, {}, "legacy", "resolve_acquire_rejection"],
    [{claimed_by: null}, {}, "legacy", "reconcile_lease_owner"],
    [{}, {required_capabilities: ["code_review"]}, "legacy", "resolve_lifecycle_edit"],
    [{}, {status: "blocked"}, "legacy", "resolve_lifecycle_edit"],
    [{}, {}, "soft_claim", "resolve_acquire_rejection"],
  ] as const) {
    const {store, request} = await seeded(overrides);
    const head = await store.loadAuthority();
    if (head.status !== "loaded") assert.fail("missing fixture");
    await store.commitAuthority({operation_id: "retired-lease", expected_provider_revision: head.provider_revision,
      events: [], receipts: [], next_projection: {...head.head, handoff_mode: mode,
        leases: [{todo_id: "todo_a", owner: "agent-a", idempotency_key: "old-key",
          version: 2, lease_epoch: 1, status: "released", expires_at: "2026-09-06T00:00:00Z", write_scopes: []}]}});
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoUpdate(store, {...request, planning_intent: intent});
    assert.equal(result.status, "failed");
    assert.equal((result.recovery as Record<string, unknown>).action, expected);
    assert.equal((result.recovery as Record<string, unknown>).acquire, undefined);
    assert.deepEqual(await store.loadAuthority(), before);
  }
});

for (const blocker of ["scope", "acceptance"]) test(`recovery respects ${blocker} acquisition admission`, async () => {
  const {store, request} = await seeded({required_write_scopes: ["src/**"]});
  const head = await store.loadAuthority();
  if (head.status !== "loaded") assert.fail("missing fixture");
  const other = todo({todo_id: "todo_other", claimed_by: "agent-b"});
  const todos = [...head.head.todos as Record<string, unknown>[], other];
  await store.commitAuthority({operation_id: "scope-conflict", expected_provider_revision: head.provider_revision,
    events: [], receipts: [], next_projection: {...head.head, handoff_mode: "legacy", todos,
      todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 2,
        records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]},
      leases: [
        {todo_id: "todo_a", owner: "agent-a", idempotency_key: "released-key", version: 2,
          lease_epoch: 1, status: "released", expires_at: "2026-09-06T00:00:00Z", write_scopes: []},
        {todo_id: "todo_other", owner: "agent-b", idempotency_key: "foreign-active-key", version: 1,
          lease_epoch: 1, status: "active", expires_at: "2026-09-06T00:00:00Z", write_scopes: blocker === "scope" ? ["src/file.ts"] : []},
      ]}});
  if (blocker === "acceptance") {
    const current = await store.loadAuthority();
    if (current.status !== "loaded") assert.fail("missing fixture");
    const configured = await configureGoalAcceptance(store, {goal_id: "goal-a", operation_id: "acceptance-hold",
      actor_agent_id: null, expected_provider_revision: current.provider_revision,
      document: {objective: "Validate selected work", non_goals: [],
        scope: {kind: "selected_work", todo_ids: ["todo_a"]}, bindings: [],
        criteria: [{id: "check", description: "Independent validation", validation_argv: ["true"], validation_timeout_seconds: 10}]}});
    assert.equal(configured.status, "applied");
  }
  const before = await store.loadAuthority();
  const result = await executeCoordinationTodoUpdate(store, request);
  const recovery = result.recovery as Record<string, unknown>;
  assert.equal(recovery.action, "resolve_acquire_rejection");
  assert.equal(recovery.reason_code, blocker === "scope" ? "write_scope_conflict" : "goal_acceptance_unbound");
  assert.equal(recovery.acquire, undefined);
  assert.equal(JSON.stringify(result).includes("foreign-active-key"), false);
  assert.deepEqual(await store.loadAuthority(), before);
});
