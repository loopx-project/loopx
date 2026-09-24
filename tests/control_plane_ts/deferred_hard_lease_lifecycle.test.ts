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
import {executeCoordinationTodoTerminalLifecycle, type CoordinationTodoTerminalLifecycleInput} from
  "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {leaseOwnerRejection} from "../../loopx/control_plane/work_items/task_lease_eligibility.ts";

const NOW = new Date("2026-09-05T23:00:00Z");
const GOAL = "synthetic-goal";
const TODO = "todo_task";
const OWNER = "agent-a";
const AGENTS = [OWNER, "agent-b"];

async function seeded(provider: "file" | "sqlite", overrides: JsonObject = {}, lease?: JsonObject) {
  const root = await mkdtemp(join(tmpdir(), `loopx-deferred-${provider}-`));
  const path = join(root, "authority");
  const store = provider === "file" ? new FileAuthorityStore(path, GOAL) : new SqliteAuthorityStore(path, GOAL);
  const todo: JsonObject = {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: TODO, role: "agent",
    status: "deferred", done: true, archive_state: "active", task_class: "advancement_task",
    text: "Resume a synthetic task", claimed_by: OWNER,
    resume_when: "resume_at:2026-09-05T22:00:00Z", ...overrides};
  if (todo.claimed_by === null) delete todo.claimed_by;
  const todos = [todo];
  const seededResult = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: GOAL, handoff_mode: "hard_lease",
      todos, leases: lease ? [lease] : [], todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA,
        todo_count: 1, records_sha256: canonicalAuthoritySha256(todos),
        contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  assert.equal(seededResult.status, "applied", JSON.stringify(seededResult));
  return store;
}

async function read(store: FileAuthorityStore | SqliteAuthorityStore) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("synthetic authority missing");
  return head;
}

function resume(operation_id: string): CoordinationTodoUpdateInput {
  return {goal_id: GOAL, todo_id: TODO, expected_role: "agent", actor_agent_id: OWNER,
    registered_agents: AGENTS, operation_id, patch: {}, clear_fields: [],
    planning_intent: {status: "open", clear_resume_when: true}, dry_run: false, now: NOW};
}

function supersede(operation_id: string): CoordinationTodoTerminalLifecycleInput {
  return {goal_id: GOAL, todo_id: TODO, expected_role: "agent", command: "supersede",
    actor_agent_id: OWNER, registered_agents: AGENTS, lifecycle_grants: [], authority_reason: null,
    decision_outcome: null, operation_identity: {kind: "explicit" as const, operation_id: operation_id}, lease_idempotency_key: null,
    lease_expected_version: null, allow_user_gate_auto_acquire: false,
    requested_no_followup: false, requested_completion_turn_key: null,
    requested_completion_identity_source: null, linked_successor_todo_ids: [], successor_intents: [],
    note: null, evidence: null, reason: "Retired synthetic wait", clear_claim: false,
    validation_declaration: null, validation_receipt: null, completion_policy_request: null,
    dry_run: false, now: NOW};
}

function oldLease(status: "active" | "released", expires_at: string): JsonObject {
  return {schema_version: "task_lease_v0", goal_id: GOAL, todo_id: TODO,
    owner: OWNER, idempotency_key: "old-execution", status, expires_at,
    version: 3, lease_epoch: 2, write_scopes: [], acquire_ttl_seconds: 900};
}

const sqliteSkip = sqliteRuntimeIdentity().sqlite_authority_qualified
  ? false : "requires the qualified SQLite runtime";

for (const provider of ["file", "sqlite"] as const) {
  const providerTest = (name: string, run: () => Promise<void>) =>
    test(name, {skip: provider === "sqlite" && sqliteSkip}, run);
  providerTest(`${provider}: deferred resume is one CAS transition and never grants execution`, async () => {
    const store = await seeded(provider);
    const before = await read(store);
    assert.equal(leaseOwnerRejection({status: "deferred", claimed_by: OWNER, excluded_agents: []}, OWNER, AGENTS),
      "todo_not_open");
    const input = resume("resume-once");
    assert.equal((await executeCoordinationTodoUpdate(store, {...input, dry_run: true})).status, "planned");
    assert.deepEqual(await read(store), before);
    const applied = await executeCoordinationTodoUpdate(store, input);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    assert.equal((applied.deferred_resume_transition as JsonObject).execution_authority_granted, false);
    const after = await read(store);
    const todo = (after.head.todos as JsonObject[])[0]!;
    assert.equal(todo.status, "open");
    assert.equal(todo.done, false);
    assert.equal(todo.resume_when, undefined);
    assert.equal(todo.claimed_by, OWNER);
    assert.deepEqual(after.head.leases, []);
    assert.equal(leaseOwnerRejection({status: String(todo.status), claimed_by: OWNER,
      excluded_agents: []}, OWNER, AGENTS), null);
    assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "replayed");
    assert.deepEqual(await read(store), after);
    assert.equal((await executeCoordinationTodoUpdate(store, {...input,
      planning_intent: {...input.planning_intent, reason: "Different"}})).reason_code,
      "coordination_operation_identity_mismatch");
  });

  providerTest(`${provider}: expired lease is retired atomically, while live execution and foreign edits fail closed`, async () => {
    const expired = await seeded(provider, {}, oldLease("active", "2026-09-05T22:30:00Z"));
    const resumed = await executeCoordinationTodoUpdate(expired, resume("retire-expired"));
    assert.equal(resumed.status, "applied", JSON.stringify(resumed));
    const head = await read(expired);
    assert.equal((head.head.leases as JsonObject[])[0]!.status, "released");
    assert.equal((head.head.leases as JsonObject[])[0]!.version, 3);
    assert.equal((head.head.todos as JsonObject[])[0]!.status, "open");

    const active = await seeded(provider, {}, oldLease("active", "2026-09-06T00:00:00Z"));
    const before = await read(active);
    for (const [change, code] of [
      [{}, "deferred_resume_active_lease"],
      [{actor_agent_id: "agent-b"}, "update_owner_mismatch"],
      [{lease_idempotency_key: "old-execution", lease_expected_version: 3},
        "deferred_resume_execution_proof_not_allowed"],
      [{patch: {note: "Bundled work"}}, "handoff_mode_lease_claim_divergence"],
    ] as const) {
      const result = await executeCoordinationTodoUpdate(active, {...resume("rejected"), ...change});
      assert.equal(result.reason_code, code, JSON.stringify(result));
      assert.deepEqual(await read(active), before);
    }
  });

  providerTest(`${provider}: an unclaimed deferred Todo stays unclaimed; excluded actors cannot reopen it`, async () => {
    const unclaimed = await seeded(provider, {claimed_by: null});
    const applied = await executeCoordinationTodoUpdate(unclaimed, resume("unclaimed-resume"));
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const head = await read(unclaimed);
    assert.equal((head.head.todos as JsonObject[])[0]!.status, "open");
    assert.equal((head.head.todos as JsonObject[])[0]!.claimed_by, undefined);

    const excluded = await seeded(provider, {excluded_agents: [OWNER]});
    const before = await read(excluded);
    const rejected = await executeCoordinationTodoUpdate(excluded, resume("excluded-resume"));
    assert.equal(rejected.reason_code, "actor_excluded", JSON.stringify(rejected));
    assert.deepEqual(await read(excluded), before);
  });

  providerTest(`${provider}: deferred supersede closes one Todo and retires expired lease lineage`, async () => {
    const store = await seeded(provider, {}, oldLease("active", "2026-09-05T22:30:00Z"));
    const input = supersede("supersede-once");
    const before = await read(store);
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, {...input, dry_run: true})).status, "planned");
    assert.deepEqual(await read(store), before);
    const applied = await executeCoordinationTodoTerminalLifecycle(store, input);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const after = await read(store);
    assert.equal((after.head.todos as JsonObject[])[0]!.status, "done");
    assert.equal((after.head.leases as JsonObject[])[0]!.status, "released");
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, input)).status, "replayed");
    assert.deepEqual(await read(store), after);
  });
}
