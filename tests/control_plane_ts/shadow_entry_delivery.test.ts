import assert from "node:assert/strict";
import {readFile, writeFile, rm, symlink} from "node:fs/promises";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {deliverShadowEntry} from "../../loopx/control_plane/coordination/shadow_entry_delivery.ts";
import {outboxEntryFileName} from "../../loopx/control_plane/coordination/local_authority_shadow_outbox.ts";
import {fixture, pendingEntry, settleFiles, sha, todo, entrySelection as selection} from "./shadow_file_fixture.ts";

const GOAL_A = {
  goal_id: "goal-a",
  goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
};
const GOAL_B = {
  goal_id: "goal-a",
  goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
};

function entryPath(r: JsonObject, phase: "prepared" | "committed" = "prepared"): string {
  return join(String(r.runtime_root), "authority-shadow", "outbox", String(r.goal_id), String(r.partition),
    outboxEntryFileName(Number(r.seq), String(r.entry_id), phase));
}

test("delivery derives marked commits and replays exact receipts after cleanup", async t => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
  const r = selection(request);
  const first = await deliverShadowEntry(r);
  assert.equal(first.outcome, "delivered");
  assert.equal(first.resolution, "committed");
  assert.equal(first.partition_digest, request.partition_digest);
  await settleFiles(f, request, first);
  const replay = await deliverShadowEntry(r);
  assert.equal(replay.outcome, "replayed");
  assert.equal(replay.resolution, first.resolution);
  assert.equal(replay.partition_digest, first.partition_digest);
  assert.equal(replay.provider_revision, first.provider_revision);
  assert.equal((await deliverShadowEntry({...r, prepared_sha256: sha("changed")})).reason_code, "outbox_receipt_mismatch");
});

test("exact delivery rejects an entry after the Goal alias is recreated", async t => {
  const f = await fixture(t, GOAL_A);
  const request = await pendingEntry(
    f,
    1,
    {handoff_mode: "hard_lease", todos: [todo()]},
  );
  const before = await f.store.loadAuthority();
  const rejected = await deliverShadowEntry({
    ...selection(request),
    goal_ref: GOAL_B,
  });
  assert.equal(rejected.outcome, "failed");
  assert.equal(rejected.reason_code, "stale_goal_instance");
  assert.deepEqual(await f.store.loadAuthority(), before);

  const delivered = await deliverShadowEntry(selection(request));
  assert.equal(delivered.outcome, "delivered");
});

for (const state of ["committed", "abandoned", "foreign"] as const) {
  test(`markerless Todo chooses ${state} from locked source bytes`, async t => {
    const f = await fixture(t);
    const old = await readFile(f.statePath);
    const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]}, {marker: false});
    if (state === "abandoned") await writeFile(f.statePath, old);
    if (state === "foreign") await writeFile(f.statePath, "Unrecorded primary state");
    const r = selection(request), before = await f.store.loadAuthority();
    const result = await deliverShadowEntry(r);
    if (state === "foreign") {
      assert.equal(result.reason_code, "source_transaction_unproved");
      assert.deepEqual(await f.store.loadAuthority(), before);
      return;
    }
    assert.equal(result.outcome, "delivered");
    assert.equal(result.resolution, state === "committed" ? "committed_proven_by_readback" : "abandoned");
    assert.equal(result.no_op, state === "abandoned");
    assert.equal(result.partition_digest, state === "abandoned" ? null : request.partition_digest);
    const loaded = await f.store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status === "loaded") assert.equal((loaded.head.todos as unknown[]).length, state === "committed" ? 1 : 0);
    const replay = await deliverShadowEntry(r);
    assert.equal(replay.outcome, "replayed");
    assert.equal(replay.resolution, result.resolution);
  });
}

test("later A→B→A evidence cannot turn a missing marker into abandonment", async t => {
  const f = await fixture(t), old = await readFile(f.statePath);
  const first = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]}, {marker: false});
  await pendingEntry(f, 2, {handoff_mode: "hard_lease", todos: []});
  await writeFile(f.statePath, old);
  const before = await f.store.loadAuthority();
  assert.equal((await deliverShadowEntry(selection(first))).reason_code, "source_transaction_unproved");
  assert.deepEqual(await f.store.loadAuthority(), before);
});

for (const field of ["resolution", "partition_projection", "partition_digest", "source_path"]) {
  test(`caller cannot supply ${field}`, async t => {
    const f = await fixture(t);
    const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
    const before = await f.store.loadAuthority();
    await assert.rejects(deliverShadowEntry({...selection(request), [field]: "invented"}), /shadow_entry_selection_invalid/);
    assert.deepEqual(await f.store.loadAuthority(), before);
  });
}

for (const changed of ["prepared", "marker", "symlink", "stale_lineage"] as const) {
  test(`changed ${changed} is rejected without a candidate commit`, async t => {
    const f = await fixture(t);
    const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
    const r = selection(request), before = await f.store.loadAuthority();
    if (changed === "prepared" || changed === "marker") {
      const path = entryPath(r, changed === "marker" ? "committed" : "prepared");
      await writeFile(path, Buffer.concat([await readFile(path), Buffer.from("\n")]));
    } else if (changed === "symlink") {
      const path = entryPath(r), other = join(f.root, "aliased.json");
      await writeFile(other, await readFile(path)); await rm(path); await symlink(other, path);
    } else r.capture_lineage_id = "another-generation";
    const result = await deliverShadowEntry(r);
    assert.equal(result.outcome, "failed");
    assert.deepEqual(await f.store.loadAuthority(), before);
  });
}

test("concurrent selectors get one commit and the same derived resolution", async t => {
  const f = await fixture(t);
  const r = selection(await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]}, {marker: false}));
  const results = await Promise.all([deliverShadowEntry(r), deliverShadowEntry(r)]);
  assert.deepEqual(results.map(r => r.outcome).sort(), ["delivered", "replayed"]);
  assert.ok(results.every(r => r.resolution === "committed_proven_by_readback" && typeof r.partition_digest === "string"));
});

for (const state of ["committed", "abandoned", "foreign"] as const) {
  test(`native lease capture recovers ${state} from full source bytes`, async t => {
    const {beginLeaseOutboxEntry} = await import("../../loopx/control_plane/coordination/local_authority_shadow_outbox.ts");
    const {requireShadowCaptureBinding} = await import("../../loopx/control_plane/coordination/shadow_management.ts");
    const {LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA: schema} = await import("../../loopx/control_plane/coordination/coordination_state_contract.generated.ts");
    const f = await fixture(t), directory = join(f.root, "goals", "goal-a", "task-leases");
    const lease = {schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_one", owner: "agent-a",
      version: 1, lease_epoch: 1, status: "active", updated_at: "2026-09-06T00:00:00Z"};
    const capture = await beginLeaseOutboxEntry({runtime_root: f.root, goal_id: "goal-a", lease_directory: directory,
      write_class: "task_lease_acquire", operation_id: "acquire-test", previous_lease: null, planned_lease: lease, active_todo_ids: null});
    assert.equal(capture.failure, null);
    const r = {schema_version: schema, runtime_root: f.root, goal_id: "goal-a", partition: "leases",
      seq: capture.seq, entry_id: capture.entry_id, capture_lineage_id: (await requireShadowCaptureBinding(f.root, "goal-a")).capture_lineage_id,
      prepared_sha256: "", committed_sha256: null};
    r.prepared_sha256 = sha(await readFile(entryPath(r)));
    if (state !== "abandoned") await writeFile(join(directory, "todo_one.json"),
      `${JSON.stringify(state === "foreign" ? {...lease, owner: "agent-b"} : lease, null, 2)}\n`);
    const before = await f.store.loadAuthority(), outcome = await deliverShadowEntry(r);
    if (state === "foreign") {
      assert.equal(outcome.reason_code, "source_transaction_unproved");
      assert.deepEqual(await f.store.loadAuthority(), before);
    } else {
      assert.equal(outcome.outcome, "delivered", JSON.stringify(outcome));
      assert.equal(outcome.resolution, state === "committed" ? "committed_proven_by_readback" : "abandoned");
      assert.equal(outcome.no_op, state === "abandoned");
    }
  });
}

test("malformed UTF-8 is not silently repaired before JSON validation", async t => {
  const f = await fixture(t);
  const r = selection(await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]}));
  const bytes = await readFile(entryPath(r));
  const at = bytes.indexOf("Qualify");
  assert.ok(at > 0);
  bytes[at] = 0xff; // Buffer.toString would replace this inside an otherwise valid JSON string.
  await writeFile(entryPath(r), bytes);
  r.prepared_sha256 = sha(bytes);
  const before = await f.store.loadAuthority();
  assert.equal((await deliverShadowEntry(r)).outcome, "failed");
  assert.deepEqual(await f.store.loadAuthority(), before);
});

test("retained receipt replay validates the complete lineage, not only its id", async t => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
  const r = selection(request), first = await deliverShadowEntry(r);
  assert.equal(first.outcome, "delivered");
  await settleFiles(f, request, first);
  // A real unrelated candidate commit has no capture-source receipt and breaks lineage.
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  await f.store.commitAuthority({expected_provider_revision: head.provider_revision, operation_id: "foreign-write",
    next_projection: head.head, receipts: [], events: []});
  assert.equal((await deliverShadowEntry(r)).outcome, "failed");
});

test("markerless recovery holds the primary lock through the real candidate commit", async t => {
  const {acquireFileMutationLock} = await import("../../loopx/control_plane/effect_runtime_io.ts");
  const f = await fixture(t);
  const r = selection(await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]}, {marker: false}));
  const commit = f.store.commitAuthority.bind(f.store);
  let observed = false;
  f.store.commitAuthority = async request => {
    await assert.rejects(acquireFileMutationLock(f.statePath, process.pid, 0), {code: "mutation_lock_timeout"});
    observed = true;
    return await commit(request);
  };
  assert.equal((await deliverShadowEntry(r, {openStore: () => f.store})).outcome, "delivered");
  assert.equal(observed, true);
});

test("duplicate lease identities reject before candidate delivery", async t => {
  const f = await fixture(t);
  const lease = {schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_one", owner: "agent-a",
    version: 1, lease_epoch: 1, status: "active", updated_at: "2026-09-06T00:00:00Z"};
  const r = selection(await pendingEntry(f, 1, {leases: [lease, lease]}, {partition: "leases"}));
  const before = await f.store.loadAuthority();
  assert.equal((await deliverShadowEntry(r)).reason_code, "source_lease_identity_mismatch");
  assert.deepEqual(await f.store.loadAuthority(), before);
});
