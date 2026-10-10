import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";

async function fixture(t: test.TestContext, todoPatch = {}, leasePatch = {}) {
  const root = await mkdtemp(join(tmpdir(), "loopx-soft-copy-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const store = new FileAuthorityStore(root, "goal-a");
  const todos = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Original acceptance", note: "Original note",
    archive_state: "active", claimed_by: "agent-a", ...todoPatch}];
  const lease = {todo_id: "todo_a", owner: "agent-a", status: "released",
    idempotency_key: "old-execution", version: 4, lease_epoch: 2,
    expires_at: "2026-09-04T00:00:00Z", ...leasePatch};
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim",
      todos, leases: [lease], todo_read_model: coordinationTodoReadModel(todos, TODO_DOMAIN_READ_RECORD_SCHEMA)}});
  const request = {goal_id: "goal-a", todo_id: "todo_a", expected_role: "agent",
    actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
    operation_id: "copy-update", patch: {note: "Continued observation"}, clear_fields: [],
    dry_run: false, now: new Date("2026-09-05T23:00:00Z")};
  return {store, request, lease};
}

test("soft claim owner copy edit preserves released history and receipt replay", async t => {
  const {store, request, lease} = await fixture(t);
  const before = await store.loadAuthority();
  assert.equal((await executeCoordinationTodoUpdate(store, {...request, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
  const after = await store.loadAuthority();
  if (after.status !== "loaded") assert.fail("missing authority");
  assert.deepEqual(after.head.leases, [lease]);
  assert.equal((after.head.todos as Record<string, unknown>[])[0]!.note, request.patch.note);
  assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), after);
  assert.equal((await executeCoordinationTodoUpdate(store, {...request, operation_id: "clear-note",
    patch: {text: "Reviewed acceptance"}, clear_fields: ["note"]})).status, "applied");
});

for (const [label, todo, lease, request] of [
  ["old explicit proof", {}, {}, {lease_idempotency_key: "old-execution", lease_expected_version: 4}],
  ["partial proof", {}, {}, {lease_idempotency_key: "old-execution"}],
  ["active", {}, {status: "active", expires_at: "2026-09-06T00:00:00Z"}, {}],
  ["expired unreleased", {}, {status: "active"}, {}],
  ["foreign history", {}, {owner: "agent-b"}, {}],
  ["foreign claim", {claimed_by: "agent-b"}, {}, {}],
  ["excluded", {excluded_agents: ["agent-a"]}, {}, {}],
  ["bound elsewhere", {bound_agent: "agent-b"}, {}, {}],
  ["blocked", {status: "blocked"}, {}, {}],
  ["unknown actor", {}, {}, {actor_agent_id: "unknown"}],
  ["missing actor", {}, {}, {actor_agent_id: null}],
  ["malformed epoch", {}, {lease_epoch: -1}, {}],
  ["ownership bundle", {}, {}, {planning_intent: {clear_claim: true}}],
  ["work requirement bundle", {}, {}, {planning_intent: {required_write_scopes: ["src/**"]}}],
  ["status bundle", {}, {}, {planning_intent: {status: "blocked"}}],
] as const) test(`soft claim copy edit rejects ${label} without effects`, async t => {
  const seeded = await fixture(t, todo, lease);
  const before = await seeded.store.loadAuthority();
  for (const dry_run of [true, false]) {
    const result = await executeCoordinationTodoUpdate(seeded.store, {...seeded.request, ...request, dry_run});
    assert.equal(result.status, "failed");
    if (label === "old explicit proof" || label === "partial proof") {
      assert.match(String((result.recovery as Record<string, unknown>).reason), /without either task-lease proof flag/);
      assert.equal((result.recovery as Record<string, unknown>).acquire, undefined);
    }
    assert.deepEqual(await seeded.store.loadAuthority(), before);
    assert.equal((await seeded.store.readReceipt(seeded.request.operation_id)).status, "missing");
  }
});
