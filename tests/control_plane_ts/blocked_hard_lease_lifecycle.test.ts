import assert from "node:assert/strict";
import {mkdtemp} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT} from
  "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationTodoUpdate, type CoordinationTodoUpdateInput} from
  "../../loopx/control_plane/coordination/todo_update.ts";

const NOW = new Date("2026-09-25T06:00:00Z");
const GOAL = "synthetic-goal";
const TODO = "todo_paused";
const OWNER = "agent-a";
const CONTROLLER = "agent-b";
const AGENTS = [OWNER, CONTROLLER];
const sqliteSkip = sqliteRuntimeIdentity().sqlite_authority_qualified
  ? false : "requires the qualified SQLite runtime";

function lease(expires_at: string): JsonObject {
  return {schema_version: "task_lease_v0", goal_id: GOAL, todo_id: TODO,
    owner: OWNER, idempotency_key: "old-execution", status: "active", expires_at,
    version: 29, lease_epoch: 29, write_scopes: [], acquire_ttl_seconds: 900};
}

async function seeded(provider: "file" | "sqlite", expires_at: string,
  resume_when?: string) {
  const root = await mkdtemp(join(tmpdir(), `loopx-blocked-${provider}-`));
  const path = join(root, "authority");
  const store = provider === "file" ? new FileAuthorityStore(path, GOAL) :
    new SqliteAuthorityStore(path, GOAL);
  const todo: JsonObject = {schema_version: TODO_DOMAIN_ITEM_SCHEMA,
    todo_id: TODO, role: "agent", status: "open", done: false,
    archive_state: "active", task_class: "advancement_task",
    text: "Paused research", claimed_by: OWNER, priority: "P4",
    completion_validation_required: true,
    successor_todo_ids: ["todo_successor"],
    resume_condition: {resume_when: "todo_done:todo_successor", satisfied: false},
    resume_ready: false,
    ...(resume_when === undefined ? {} : {resume_when})};
  const todos = [todo];
  const result = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: GOAL, handoff_mode: "hard_lease",
      todos, leases: [lease(expires_at)],
      todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA,
        todo_count: 1, records_sha256: canonicalAuthoritySha256(todos),
        contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  assert.equal(result.status, "applied", JSON.stringify(result));
  return store;
}

async function read(store: FileAuthorityStore | SqliteAuthorityStore) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("synthetic authority missing");
  return head;
}

function pause(operation_id: string): CoordinationTodoUpdateInput {
  return {goal_id: GOAL, todo_id: TODO, expected_role: "agent",
    actor_agent_id: CONTROLLER, registered_agents: AGENTS, operation_id,
    lifecycle_grants: [{agent_id: CONTROLLER, actions: ["update"], requires_reason: true}],
    authority_reason: "Owner-directed pause of research", patch: {}, clear_fields: [],
    planning_intent: {status: "blocked", reason: "Owner paused this lane",
      clear_resume_when: true}, dry_run: false, now: NOW};
}

for (const provider of ["file", "sqlite"] as const) {
  const providerTest = (name: string, run: () => Promise<void>) =>
    test(name, {skip: provider === "sqlite" && sqliteSkip}, run);

  providerTest(`${provider}: blocked lifecycle retires expired lease with Todo in one CAS`, async () => {
    const store = await seeded(provider, "2026-09-25T05:00:00Z", "todo_done:todo_successor");
    const input = pause("pause-once");
    const before = await read(store);
    const planned = await executeCoordinationTodoUpdate(store, {...input, dry_run: true});
    assert.equal(planned.status, "planned", JSON.stringify(planned));
    assert.deepEqual(await read(store), before);
    const applied = await executeCoordinationTodoUpdate(store, input);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    assert.equal((applied.blocked_lifecycle_transition as JsonObject).execution_authority_granted, false);
    assert.equal((applied.blocked_lifecycle_transition as JsonObject).lease_retirement, "released");
    const after = await read(store);
    const todo = (after.head.todos as JsonObject[])[0]!;
    assert.equal(todo.status, "blocked");
    assert.equal(todo.done, false);
    assert.equal(todo.reason, "Owner paused this lane");
    assert.equal(todo.resume_when, undefined);
    assert.equal(todo.resume_condition, undefined);
    assert.equal(todo.resume_ready, undefined);
    assert.equal(todo.claimed_by, OWNER);
    assert.equal(todo.completion_validation_required, true);
    assert.deepEqual(todo.successor_todo_ids, ["todo_successor"]);
    const retired = (after.head.leases as JsonObject[])[0]!;
    assert.equal(retired.status, "released");
    assert.equal(retired.version, 29);
    assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "replayed");
    assert.deepEqual(await read(store), after);
    assert.equal((await executeCoordinationTodoUpdate(store, {...input,
      planning_intent: {...input.planning_intent, reason: "A different pause"}})).reason_code,
      "coordination_operation_identity_mismatch");
  });

  providerTest(`${provider}: stale derived wait without resume_when clears on pause and can reopen`, async () => {
    const store = await seeded(provider, "2026-09-25T05:00:00Z");
    assert.equal((await executeCoordinationTodoUpdate(store, pause("pause-stale-wait"))).status, "applied");
    const paused = await read(store);
    assert.equal((paused.head.todos as JsonObject[])[0]!.resume_condition, undefined);
    const reopened = await executeCoordinationTodoUpdate(store, {...pause("reopen-once"),
      actor_agent_id: OWNER, lifecycle_grants: [], authority_reason: null,
      planning_intent: {status: "open", clear_resume_when: true,
        reason: "Owner resumed research"}});
    assert.equal(reopened.status, "applied", JSON.stringify(reopened));
    assert.equal((reopened.blocked_lifecycle_transition as JsonObject).execution_authority_granted, false);
    const after = await read(store);
    assert.equal((after.head.todos as JsonObject[])[0]!.status, "open");
    assert.equal((after.head.leases as JsonObject[])[0]!.status, "released");
  });

  providerTest(`${provider}: typed prerequisite wait preserves pause fencing and grants no execution`, async () => {
    const store = await seeded(provider, "2026-09-25T05:00:00Z");
    const input = {...pause("pause-typed-wait"), planning_intent: {
      status: "blocked", reason: "Await the identified prerequisite", resume_when: "todo_done:todo_successor"}};
    assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "applied");
    const after = await read(store), todo = (after.head.todos as JsonObject[])[0]!;
    assert.equal(todo.status, "blocked");
    assert.equal(todo.resume_when, "todo_done:todo_successor");
    assert.equal(todo.claimed_by, OWNER);
    assert.equal((after.head.leases as JsonObject[])[0]!.status, "released");
    assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "replayed");
    assert.deepEqual(await read(store), after);
    const active = await seeded(provider, "2026-09-25T07:00:00Z");
    const before = await read(active);
    assert.equal((await executeCoordinationTodoUpdate(active, input)).reason_code, "blocked_lifecycle_active_lease");
    assert.equal((await executeCoordinationTodoUpdate(active, {...input,
      lease_idempotency_key: "old-execution", lease_expected_version: 29})).reason_code,
      "blocked_lifecycle_execution_proof_not_allowed");
    assert.deepEqual(await read(active), before);
  });

  providerTest(`${provider}: live lease, unauthorized actor and bundled work are rejected`, async () => {
    const active = await seeded(provider, "2026-09-25T07:00:00Z");
    const before = await read(active);
    const cases: Array<[Partial<CoordinationTodoUpdateInput>, string]> = [
      [{}, "blocked_lifecycle_active_lease"],
      [{lifecycle_grants: []}, "update_owner_mismatch"],
      [{lease_idempotency_key: "old-execution", lease_expected_version: 29},
        "blocked_lifecycle_execution_proof_not_allowed"],
      [{patch: {note: "Bundled work"}}, "lease_fence_required"],
    ];
    for (const [change, code] of cases) {
      const result = await executeCoordinationTodoUpdate(active, {...pause("reject-pause"), ...change});
      assert.equal(result.reason_code, code, JSON.stringify(result));
      assert.deepEqual(await read(active), before);
    }
  });
}
