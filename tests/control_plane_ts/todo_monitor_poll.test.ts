import assert from "node:assert/strict";
import {mkdtemp} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationMonitorPoll} from "../../loopx/control_plane/coordination/todo_monitor_poll.ts";
import {selectMonitorTodo} from "../../loopx/control_plane/scheduler/monitor_successor.ts";

async function seeded(overrides: JsonObject = {}, extras: JsonObject[] = []) {
  const store = new FileAuthorityStore(await mkdtemp(join(tmpdir(), "monitor-transaction-")), "goal-a");
  const records = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_monitor", role: "agent",
    status: "open", done: false, text: "Observe external progress", archive_state: "active",
    task_class: "continuous_monitor", target_key: "watch-a", cadence: "1h", ...overrides}, ...extras];
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: "goal-a", todos: records, leases: [],
      todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: records.length,
        records_sha256: canonicalAuthoritySha256(records), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const request = {goal_id: "goal-a", operation_id: "poll-a", actor_agent_id: "agent-a",
    registered_agents: ["agent-a", "agent-b"], dry_run: false,
    observation: {todo_id: "todo_monitor", target_key: "watch-a", generated_at: "2026-09-01T00:00:00Z",
      result_hash: "result-a", material_change: true},
    intent: {next_agent_todo: "Deliver the newly observed result", next_action_kind: "implementation"}};
  return {store, request};
}

test("monitor and independent successor commit together, preview is inert, receipt replays exact intent", async () => {
  const {store, request} = await seeded();
  const before = await store.loadAuthority();
  assert.equal((await executeCoordinationMonitorPoll(store, {...request, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  const result = await executeCoordinationMonitorPoll(store, request);
  assert.equal(result.status, "applied", JSON.stringify(result));
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  const todos = after.head.todos as JsonObject[];
  assert.equal(todos.length, 2);
  const monitor = todos.find(todo => todo.todo_id === "todo_monitor")!;
  assert.equal(monitor.material_change_generation, 1);
  assert.equal(monitor.status, "open");
  assert.equal(monitor.claimed_by, undefined);
  const successor = todos.find(todo => todo.todo_id !== monitor.todo_id)!;
  assert.equal(successor.task_class, "advancement_task");
  assert.equal(successor.unblocks_todo_id, monitor.todo_id);
  assert.equal(successor.continuation_policy, "independent_handoff");
  assert.equal((await executeCoordinationMonitorPoll(store, request)).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), after);
  assert.equal((await executeCoordinationMonitorPoll(store, {...request,
    intent: {...request.intent, next_agent_todo: "Different intent"}})).reason_code,
  "coordination_operation_identity_mismatch");
});

test("invalid successor, inactive target, ownership and exclusion reject before any write", async () => {
  for (const overrides of [{status: "done", done: true}, {archive_state: "archive"},
    {claimed_by: "agent-b"}, {excluded_agents: ["agent-a"]}, {bound_agent: "agent-b"}]) {
    const {store, request} = await seeded(overrides);
    const before = await store.loadAuthority();
    assert.equal((await executeCoordinationMonitorPoll(store, request)).status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  }
  const {store, request} = await seeded();
  const before = await store.loadAuthority();
  for (const intent of [{next_agent_todo: "Missing routing"},
    {...request.intent, next_claimed_by: "unknown"}]) {
    assert.equal((await executeCoordinationMonitorPoll(store, {...request, intent})).status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
  }
});

test("unchanged observations reschedule without creating delivery work", async () => {
  const {store, request} = await seeded();
  const result = await executeCoordinationMonitorPoll(store, {...request, intent: {},
    observation: {...request.observation, material_change: false}});
  assert.equal(result.status, "applied", JSON.stringify(result));
  const receipt = result.writeback as JsonObject;
  assert.equal(receipt.consecutive_no_change, 1);
  assert.equal(receipt.material_change_generation, 0);
  assert.equal(receipt.next_due_at, "2026-09-01T01:00:00Z");
  assert.deepEqual(receipt.next_todos, []);
});

test("User successors use public actor-bound authoring scope, never global gates", async () => {
  for (const taskClass of ["user_action", "user_gate"]) {
    const {store, request} = await seeded();
    const result = await executeCoordinationMonitorPoll(store, {...request,
      intent: {next_user_todo: "Review observed change", next_user_task_class: taskClass}});
    assert.equal(result.status, "applied", JSON.stringify(result));
    const todo = ((result.writeback as JsonObject).next_todos as JsonObject[])[0]!;
    assert.equal(todo.bound_agent, "agent-a");
    assert.equal(todo.global_gate, undefined);
    assert.equal(todo.blocks_agent, taskClass === "user_gate" ? "agent-a" : undefined);
  }
});

test("same hash cannot author duplicate work; distinct later evidence advances generation", async () => {
  const {store, request} = await seeded();
  assert.equal((await executeCoordinationMonitorPoll(store, request)).status, "applied");
  const before = await store.loadAuthority();
  const later = {...request, operation_id: "poll-b", observation: {...request.observation, generated_at: "2026-09-01T01:00:00Z"}};
  assert.equal((await executeCoordinationMonitorPoll(store, later)).status, "failed");
  assert.deepEqual(await store.loadAuthority(), before);
  const changed = await executeCoordinationMonitorPoll(store, {...later,
    observation: {...later.observation, result_hash: "result-b"},
    intent: {...request.intent, next_agent_todo: "Deliver a different change"}});
  assert.equal(changed.status, "applied", JSON.stringify(changed));
  assert.equal((changed.writeback as JsonObject).material_change_generation, 2);
});

test("retained lease and stale observation cannot mutate either half", async () => {
  const {store, request} = await seeded();
  assert.equal((await executeCoordinationMonitorPoll(store, request)).status, "applied");
  const before = await store.loadAuthority();
  assert.equal(before.status, "loaded");
  if (before.status !== "loaded") return;
  assert.equal((await executeCoordinationMonitorPoll(store, {...request, operation_id: "stale",
    observation: {...request.observation, generated_at: "2025-01-01T00:00:00Z"}})).status, "failed");
  assert.deepEqual(await store.loadAuthority(), before);
  await store.commitAuthority({operation_id: "lease", expected_provider_revision: before.provider_revision,
    events: [], receipts: [], next_projection: {...before.head, leases: [{todo_id: "todo_monitor",
      owner: "agent-a", status: "active", expires_at: "2099-01-01T00:00:00Z", version: 1, idempotency_key: "execution"}]}});
  const leased = await store.loadAuthority();
  assert.equal((await executeCoordinationMonitorPoll(store, {...request, operation_id: "leased"})).status, "failed");
  assert.deepEqual(await store.loadAuthority(), leased);
});

test("hard mode cannot bypass a missing lease; soft mode cannot accept supplied execution proof", async () => {
  for (const mode of ["hard_lease", "soft_claim"] as const) {
    const {store, request} = await seeded({claimed_by: "agent-a"});
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status !== "loaded") return;
    await store.commitAuthority({operation_id: "mode", expected_provider_revision: loaded.provider_revision,
      events: [], receipts: [], next_projection: {...loaded.head, handoff_mode: mode}});
    const before = await store.loadAuthority();
    for (const proof of [null, {idempotency_key: "missing-execution", expected_version: 1}]) {
      if (mode === "soft_claim" && proof === null) continue;
      const result = await executeCoordinationMonitorPoll(store, {...request, lease_proof: proof});
      assert.equal(result.status, "failed");
      assert.deepEqual(await store.loadAuthority(), before);
      assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
    }
  }
});

test("target-key selection ignores completed history but never guesses between live monitors", () => {
  const active = {todo_id: "todo_current", role: "agent", task_class: "continuous_monitor",
    target_key: "watch", status: "open", archive_state: "active"};
  const history = {...active, todo_id: "todo_history", status: "done", archive_state: "archive"};
  assert.equal(selectMonitorTodo([history, active], null, "watch").todo_id, active.todo_id);
  assert.throws(() => selectMonitorTodo([history, active], history.todo_id, "watch"), /unfinished/);
  assert.throws(() => selectMonitorTodo([active, {...active, todo_id: "todo_other"}], null, "watch"), /multiple/);
});

test("current leased Monitor atomically observes and creates work without renewing or releasing its lease", async () => {
  const {store, request} = await seeded({claimed_by: "agent-a"});
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  const lease = {schema_version: "task_lease_v0", goal_id: request.goal_id,
    todo_id: "todo_monitor", owner: "agent-a", status: "active",
    idempotency_key: "execution-a", version: 4, lease_epoch: 2,
    acquired_at: "2026-09-01T00:00:00Z", expires_at: "2026-09-01T01:00:00Z"};
  await store.commitAuthority({operation_id: "seed-lease", expected_provider_revision: head.provider_revision,
    events: [], receipts: [], next_projection: {...head.head, handoff_mode: "hard_lease", leases: [lease]}});
  const fenced = {...request, now: new Date("2026-09-01T00:05:00Z"),
    lease_proof: {idempotency_key: "execution-a", expected_version: 4}};
  const before = await store.loadAuthority();
  for (const proof of [null, {idempotency_key: "wrong", expected_version: 4},
    {idempotency_key: "execution-a", expected_version: 3}]) {
    assert.equal((await executeCoordinationMonitorPoll(store, {...fenced, lease_proof: proof})).status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
  }
  const applied = await executeCoordinationMonitorPoll(store, fenced);
  assert.equal(applied.status, "applied", JSON.stringify(applied));
  assert.deepEqual((applied.writeback as JsonObject).lease_proof, fenced.lease_proof);
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  assert.deepEqual(after.head.leases, [lease]);
  assert.equal((after.head.todos as JsonObject[]).length, 2);
  // A receipt settles past work even after the old execution expires; it
  // must not execute the observation again or grant another lease.
  assert.equal((await executeCoordinationMonitorPoll(store, {...fenced,
    now: new Date("2026-09-02T00:00:00Z")})).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), after);
  assert.equal((await executeCoordinationMonitorPoll(store, {...fenced, operation_id: "fresh-expired",
    now: new Date("2026-09-02T00:00:00Z")})).status, "failed");
});


test("guarded canonical requests require the versioned receiver contract", async () => {
  const {pollLocalCoordinationMonitor} = await import("../../loopx/control_plane/coordination/local_authority_runtime.ts");
  const oldSchema = await pollLocalCoordinationMonitor({schema_version: "loopx_coordination_monitor_poll_request_v2",
    gate_scope_guard: true});
  assert.equal(oldSchema.status, "failed");
  assert.match(String(oldSchema.reason), /requires request v3/);
  const missingGuard = await pollLocalCoordinationMonitor({schema_version: "loopx_coordination_monitor_poll_request_v3"});
  assert.equal(missingGuard.status, "failed");
  assert.match(String(missingGuard.reason), /requires gate_scope_guard=true/);
});
