import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile, writeFile, rename, symlink, unlink } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import {
  commitLocalAuthorityShadowEntry,
  localAuthorityShadowPartitionDigest,
  readLocalAuthorityShadow,
} from "../../loopx/control_plane/coordination/local_authority_shadow.ts";
import { outboxEntryIdentity, beginLeaseOutboxEntry } from "../../loopx/control_plane/coordination/local_authority_shadow_outbox.ts";
import {
  drainShadowOutbox,
  SHADOW_DRAIN_SCHEMA,
  SHADOW_EXACT_DRAIN_SCHEMA,
} from "../../loopx/control_plane/coordination/shadow_drain.ts";
import { requireShadowCaptureBinding } from "../../loopx/control_plane/coordination/shadow_management.ts";
import * as schemas from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import { fixture, pendingEntry, settleFiles, todo, sha } from "./shadow_file_fixture.ts";
import { resolveTestPython } from "../../scripts/test-python.mjs";

const execFileAsync = promisify(execFile);
const PYTHON = resolveTestPython();
const GOAL_A = {
  goal_id: "goal-a",
  goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
};
const GOAL_B = {
  goal_id: "goal-a",
  goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
};

test("released Todo manifests replay without rewriting history and allow the next capture", async (t) => {
  for (const absent of [
    ["completion_receipt_id"],
    ["completion_receipt_id", "completion_result"],
    ["completion_receipt_id", "completion_result", "completion_validation_revision", "completion_validation_revision_history"],
  ]) {
    const f = await fixture(t);
    const commit = f.store.commitAuthority.bind(f.store);
    // Write a real File transaction using a released writer's field manifest.
    f.store.commitAuthority = async (input) => {
      const next = structuredClone(input.next_projection);
      const model = next.todo_read_model as JsonObject;
      model.contract_fields = (model.contract_fields as string[]).filter((field) => !absent.includes(field));
      return commit({...input, next_projection: next});
    };
    const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
    const result = await commitLocalAuthorityShadowEntry(request, {openStore: () => f.store});
    assert.equal(result.outcome, "delivered");
    f.store.commitAuthority = commit;
    await settleFiles(f, request, result);
    const before = await readFile(f.store.path);
    const read = await readLocalAuthorityShadow({schema_version: schemas.LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
      runtime_root: f.root, goal_id: "goal-a", scan_limit: 10});
    assert.equal(read.status, "loaded", JSON.stringify(read));
    assert.deepEqual(await readFile(f.store.path), before);
    const next = await pendingEntry(f, 2, {handoff_mode: "hard_lease", todos: [todo("todo_one", "done")]},
      {writeClass: "todo_update"});
    assert.equal((await commitLocalAuthorityShadowEntry(next)).outcome, "delivered");
    const history = await f.store.scanCommitted(null, 10);
    assert.equal(history.status, "page");
    if (history.status !== "page") continue;
    assert.equal(history.transactions.length, 3);
    assert.equal(((history.transactions[1]!.projection.todo_read_model as JsonObject).contract_fields as string[])
      .includes("completion_receipt_id"), false);
    assert.equal(((history.transactions[2]!.projection.todo_read_model as JsonObject).contract_fields as string[])
      .includes("completion_receipt_id"), true);
  }
});

test("historical replay still rejects malformed manifests, record proofs and unrelated metadata", async (t) => {
  const mutations: ((head: JsonObject) => void)[] = [
    (head) => { (head.todo_read_model as JsonObject).contract_fields = ["todo_id"]; },
    (head) => { ((head.todo_read_model as JsonObject).contract_fields as string[]).reverse(); },
    (head) => { (head.todo_read_model as JsonObject).records_sha256 = "0".repeat(64); },
    (head) => { (head.todo_read_model as JsonObject).todo_count = 999; },
    (head) => { (head.todo_read_model as JsonObject).unrecognized = true; },
    (head) => { head.unrecognized = true; },
    (head) => {
      const model = head.todo_read_model as JsonObject;
      model.contract_fields = (model.contract_fields as string[]).filter((field) => field !== "completion_receipt_id");
      (head.todos as JsonObject[])[0]!.completion_receipt_id = "unavailable-in-this-revision";
    },
  ];
  for (const mutate of mutations) {
    const f = await fixture(t);
    const commit = f.store.commitAuthority.bind(f.store);
    f.store.commitAuthority = async (input) => {
      const next = structuredClone(input.next_projection); mutate(next);
      return commit({...input, next_projection: next});
    };
    const request = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
    assert.equal((await commitLocalAuthorityShadowEntry(request, {openStore: () => f.store})).outcome, "delivered");
    const before = await readFile(f.store.path);
    const read = await readLocalAuthorityShadow({schema_version: schemas.LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
      runtime_root: f.root, goal_id: "goal-a", scan_limit: 10});
    assert.equal(read.status, "failed");
    assert.deepEqual(await readFile(f.store.path), before);
  }
});

test("one primary entry commits exactly once after a complete baseline", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] });
  const result = await commitLocalAuthorityShadowEntry(request);
  assert.equal(result.outcome, "delivered"); assert.equal(result.cursor, "2");
  const loaded = await f.store.loadAuthority(); assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") return;
  assert.deepEqual(loaded.head.todos, [todo()]); assert.deepEqual(loaded.head.leases, []);
  assert.equal(loaded.head.capture_lineage_id, (request.entry as JsonObject).capture_lineage_id);
  const receipt = await f.store.readReceipt(String((request.entry as JsonObject).entry_id));
  assert.equal(receipt.status, "found");
  if (receipt.status === "found") {
    assert.equal(receipt.receipts.length, 1);
    assert.equal(receipt.receipts[0]?.prepared_sha256, (request.entry as JsonObject).prepared_sha256);
    assert.equal(receipt.receipts[0]?.source_transaction_correlated, true);
    assert.equal(receipt.receipts[0]?.parity_verdict, "not_evaluated");
  }
  await settleFiles(f, request, result);
  const replay = await commitLocalAuthorityShadowEntry(request);
  assert.equal(replay.outcome, "replayed"); assert.equal(replay.cursor, "2");
  const history = await f.store.scanCommitted(null, 10);
  assert.equal(history.status, "page"); if (history.status === "page") assert.equal(history.transactions.length, 2);
});

test("exact drain rejects stale and legacy callers before advancing Goal A", async (t) => {
  const f = await fixture(t, GOAL_A);
  await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
  const before = await f.store.loadAuthority();
  const base = {
    runtime_root: f.root,
    goal_id: "goal-a",
    python_executable: PYTHON,
    config_enabled: true,
    max_entries: 10,
    budget_seconds: 10,
    lock_timeout_seconds: 2,
  };

  const stale = await drainShadowOutbox({
    ...base,
    schema_version: SHADOW_EXACT_DRAIN_SCHEMA,
    goal_ref: GOAL_B,
  });
  assert.equal(stale.outcome, "stopped");
  assert.equal(stale.reason_code, "stale_goal_instance");
  assert.equal(stale.pending_after, 1);
  assert.deepEqual(await f.store.loadAuthority(), before);

  const legacy = await drainShadowOutbox({
    ...base,
    schema_version: SHADOW_DRAIN_SCHEMA,
  });
  assert.equal(legacy.outcome, "stopped");
  assert.equal(legacy.reason_code, "legacy_goal_binding");
  assert.deepEqual(await f.store.loadAuthority(), before);

  const current = await drainShadowOutbox({
    ...base,
    schema_version: SHADOW_EXACT_DRAIN_SCHEMA,
    goal_ref: GOAL_A,
  });
  assert.equal(current.outcome, "drained");
  assert.equal(current.delivered, 1);
});

test("receipt replay rejects every changed identity field even after pending cleanup", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] });
  const result = await commitLocalAuthorityShadowEntry(request); await settleFiles(f, request, result);
  for (const field of ["prepared_sha256", "committed_sha256", "prepared_at", "committed_at"] ) {
    const changed = structuredClone(request); const entry = changed.entry as JsonObject;
    entry[field] = field.endsWith("sha256") ? sha("foreign") : "2026-09-06T01:00:00Z";
    const replay = await commitLocalAuthorityShadowEntry(changed);
    assert.equal(replay.outcome, "protocol_mismatch", field);
  }
  const changed = structuredClone(request); ((changed.entry as JsonObject).writer as JsonObject).operation_id = "foreign-operation";
  assert.equal((await commitLocalAuthorityShadowEntry(changed)).outcome, "protocol_mismatch");
});

test("foreign root, lineage, source, sequence and digest cannot enter history", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [] });
  for (const [field, value, expected] of [
    ["capture_lineage_id", "foreign", "stale_generation"],
    ["source_root_digest", sha("foreign"), "source_root_mismatch"],
    ["entry_id", `local-shadow-tx-${"f".repeat(64)}`, "entry_identity_mismatch"],
  ]) {
    const changed = structuredClone(request); (changed.entry as JsonObject)[field!] = value!;
    assert.equal((await commitLocalAuthorityShadowEntry(changed)).reason_code, expected);
  }
  const digest = structuredClone(request); digest.partition_digest = sha("different");
  assert.equal((await commitLocalAuthorityShadowEntry(digest)).reason_code, "partition_digest_mismatch");
  const second = await pendingEntry(f, 2, { handoff_mode: "hard_lease", todos: [] });
  assert.equal((await commitLocalAuthorityShadowEntry(second)).reason_code, "partition_sequence_mismatch");
  assert.equal((await f.store.loadAuthority() as { cursor: string }).cursor, "1");
});

test("first commit verifies the actual pending bytes instead of trusting a supplied hash", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [] });
  const entry = request.entry as JsonObject;
  const path = join(f.root, "authority-shadow", "outbox", "goal-a", "todos", `0000000001-${entry.entry_id}.prepared.json`);
  await writeFile(path, `${await readFile(path, "utf8")} `);
  assert.equal((await commitLocalAuthorityShadowEntry(request)).reason_code, "outbox_prepared_bytes_mismatch");
  assert.equal((await f.store.loadAuthority() as { cursor: string }).cursor, "1");
});

test("a self-consistent foreign lineage entry cannot commit even with matching bytes and identity hashes", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] });
  const entry = request.entry as JsonObject;
  const directory = join(f.root, "authority-shadow", "outbox", "goal-a", "todos");
  const oldStem = `0000000001-${entry.entry_id}`;
  entry.capture_lineage_id = "foreign-complete-lineage";
  entry.entry_id = outboxEntryIdentity("goal-a", "todos", 1, String((entry.source as JsonObject).bytes_digest),
    String(entry.capture_lineage_id), String(entry.source_root_digest));
  const newStem = `0000000001-${entry.entry_id}`;
  for (const [suffix, digestField] of [["prepared", "prepared_sha256"], ["committed", "committed_sha256"]]) {
    const oldPath = join(directory, `${oldStem}.${suffix}.json`);
    const value = JSON.parse(await readFile(oldPath, "utf8"));
    value.entry_id = entry.entry_id; value.capture_lineage_id = entry.capture_lineage_id;
    const raw = JSON.stringify(value); await writeFile(oldPath, raw);
    await rename(oldPath, join(directory, `${newStem}.${suffix}.json`));
    entry[digestField!] = sha(raw);
  }
  const rejected = await commitLocalAuthorityShadowEntry(request);
  assert.equal(rejected.outcome, "failed");
  assert.equal(rejected.reason_code, "stale_generation");
  assert.equal((await f.store.loadAuthority() as { cursor: string }).cursor, "1");
});

test("abandoned settlement advances only settled sequence; unproved and implicit seeds hold", async (t) => {
  const f = await fixture(t);
  const abandoned = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [] }, { resolution: "abandoned", marker: false });
  const result = await commitLocalAuthorityShadowEntry(abandoned); assert.equal(result.outcome, "delivered");
  const view = await readLocalAuthorityShadow({ schema_version: schemas.LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    runtime_root: f.root, goal_id: "goal-a", receipt_operation_id: (abandoned.entry as JsonObject).entry_id, scan_limit: 10 });
  assert.equal(view.status, "loaded");
  assert.deepEqual((view.proof as JsonObject).last_sequences, { todos: 1, leases: 0 });
  assert.deepEqual((view.proof as JsonObject).last_applied_sequences, { todos: 0, leases: 0 });
  assert.equal(((view.proof as JsonObject).receipt as JsonObject).operation_id, (abandoned.entry as JsonObject).entry_id);
  const unproved = await pendingEntry(f, 2, { handoff_mode: "hard_lease", todos: [] }, { resolution: "unproved", marker: false });
  assert.equal((await commitLocalAuthorityShadowEntry(unproved)).reason_code, "source_transaction_unproved");
});

test("concurrent commit_entry callers produce one exact receipt", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] });
  const results = await Promise.all([commitLocalAuthorityShadowEntry(request), commitLocalAuthorityShadowEntry(request)]);
  assert.deepEqual(results.map((result) => result.outcome).sort(), ["delivered", "replayed"]);
});

test("a lease writer with a missing cursor obtains its next sequence from proved committed history", async (t) => {
  const f = await fixture(t);
  const lease = { schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_one", owner: "agent-a", version: 1,
    lease_epoch: 1, status: "active", updated_at: "2026-09-06T00:00:00Z" };
  const entry = await pendingEntry(f, 1, { leases: [lease] }, { partition: "leases", writeClass: "task_lease_acquire" });
  const delivered = await commitLocalAuthorityShadowEntry(entry);
  assert.equal(delivered.outcome, "delivered");
  await settleFiles(f, entry, delivered);
  const directory = join(f.root, "authority-shadow", "outbox", "goal-a", "leases");
  await unlink(join(directory, "drain-cursor.json"));
  const capture = await beginLeaseOutboxEntry({ runtime_root: f.root, goal_id: "goal-a",
    lease_directory: join(f.root, "goals", "goal-a", "task-leases"), write_class: "task_lease_renew",
    operation_id: null, previous_lease: lease, planned_lease: { ...lease, version: 2 },
    active_todo_ids: null });
  assert.equal(capture.failure, null);
  assert.equal(capture.seq, 2);
  await assert.rejects(readFile(join(directory, "drain-cursor.json")), { code: "ENOENT" });
});

test("exact lease capture rejects a recreated Goal and an unstamped caller", async (t) => {
  const f = await fixture(t, GOAL_A);
  const lease = {
    schema_version: "task_lease_v0",
    goal_id: "goal-a",
    todo_id: "todo_one",
    owner: "agent-a",
    version: 1,
    lease_epoch: 1,
    status: "active",
    updated_at: "2026-09-06T00:00:00Z",
  };
  const input = {
    runtime_root: f.root,
    goal_id: "goal-a",
    lease_directory: join(f.root, "goals", "goal-a", "task-leases"),
    write_class: "task_lease_acquire",
    operation_id: null,
    previous_lease: null,
    planned_lease: lease,
    active_todo_ids: null,
  };
  const stale = await beginLeaseOutboxEntry({...input, goal_ref: GOAL_B});
  assert.equal(stale.failure?.reason_code, "stale_goal_instance");
  assert.equal(stale.failure?.error_class, "ShadowManagementError");
  const unstamped = await beginLeaseOutboxEntry(input);
  assert.equal(unstamped.failure?.reason_code, "goal_instance_id_missing");
  assert.equal(unstamped.failure?.error_class, "ShadowManagementError");
  const current = await beginLeaseOutboxEntry({...input, goal_ref: GOAL_A});
  assert.equal(current.failure, null);
  assert.equal(current.seq, 1);
});

test("a lease writer uses the active binding digest through a runtime-root alias", async (t) => {
  const f = await fixture(t);
  const alias = `${f.root}-alias`;
  t.after(() => unlink(alias));
  await symlink(f.root, alias, process.platform === "win32" ? "junction" : "dir");
  const planned = { schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_one",
    owner: "agent-a", version: 1, lease_epoch: 1, status: "active", updated_at: "2026-09-06T00:00:00Z" };
  const capture = await beginLeaseOutboxEntry({ runtime_root: alias, goal_id: "goal-a",
    lease_directory: join(alias, "goals", "goal-a", "task-leases"), write_class: "task_lease_acquire",
    operation_id: null, previous_lease: null, planned_lease: planned, active_todo_ids: null });
  assert.equal(capture.failure, null);
  const prepared = JSON.parse(await readFile(join(alias, "authority-shadow", "outbox", "goal-a", "leases",
    `0000000001-${capture.entry_id}.prepared.json`), "utf8"));
  assert.equal(prepared.source_root_digest, (await requireShadowCaptureBinding(alias, "goal-a")).source_root_digest);
});

test("lease capture omits a lease whose Todo left the current graph", async (t) => {
  const f = await fixture(t);
  const leaseDirectory = join(f.root, "goals", "goal-a", "task-leases");
  const archivedLease = { schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_gone",
    owner: "agent-a", version: 1, lease_epoch: 1, status: "released", updated_at: "2026-09-06T00:00:00Z" };
  await writeFile(join(leaseDirectory, "todo_gone.json"), JSON.stringify(archivedLease));
  const planned = { schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_one",
    owner: "agent-a", version: 1, lease_epoch: 1, status: "active", updated_at: "2026-09-06T00:00:00Z" };
  // `todo_gone` is absent from the graph: the capture must not project it.
  const filtered = await beginLeaseOutboxEntry({ runtime_root: f.root, goal_id: "goal-a",
    lease_directory: leaseDirectory, write_class: "task_lease_acquire", operation_id: "op-1",
    previous_lease: null, planned_lease: planned, active_todo_ids: ["todo_one"] });
  assert.equal(filtered.failure, null);
  const prepared = JSON.parse(await readFile(
    join(f.root, "authority-shadow", "outbox", "goal-a", "leases",
      `0000000001-${filtered.entry_id}.prepared.json`), "utf8"));
  assert.deepEqual(prepared.projection.leases.map((item: JsonObject) => item.file_stem), ["todo_one"]);
  // A graph that still contains the Todo retains it: the rule drops orphans only.
  const retained = await beginLeaseOutboxEntry({ runtime_root: f.root, goal_id: "goal-a",
    lease_directory: leaseDirectory, write_class: "task_lease_acquire", operation_id: "op-2",
    previous_lease: null, planned_lease: planned, active_todo_ids: ["todo_one", "todo_gone"] });
  assert.equal(retained.failure, null);
  const second = JSON.parse(await readFile(
    join(f.root, "authority-shadow", "outbox", "goal-a", "leases",
      `0000000002-${retained.entry_id}.prepared.json`), "utf8"));
  assert.deepEqual(second.projection.leases.map((item: JsonObject) => item.file_stem), ["todo_gone", "todo_one"]);
});

for (const [marker, resolution, expected] of [
  [true, "committed", "delivered"],
  [true, "abandoned", "failed"],
  [true, "committed_proven_by_readback", "failed"],
  [false, "committed", "failed"],
  [false, "abandoned", "delivered"],
  [false, "committed_proven_by_readback", "delivered"],
] as const) {
  test(`marker presence ${marker} requires an independently proved ${resolution} resolution`, async (t) => {
    const f = await fixture(t);
    const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] }, { marker, resolution });
    const result = await commitLocalAuthorityShadowEntry(request);
    assert.equal(result.outcome, expected, JSON.stringify(result));
    if (marker && resolution === "committed") {
      await settleFiles(f, request, result);
      const relabelled = structuredClone(request);
      (relabelled.entry as JsonObject).resolution = "abandoned";
      relabelled.partition_projection = null; relabelled.partition_digest = null;
      assert.equal((await commitLocalAuthorityShadowEntry(relabelled)).outcome, "protocol_mismatch");
    }
  });
}

test("a missing primary mutation cannot hide behind continuous sequence numbers and a matching final projection", async (t) => {
  const f = await fixture(t);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] });
  const entry = request.entry as JsonObject;
  (entry.source as JsonObject).previous_partition_digest = sha("unrecorded intermediate canonical state");
  const path = join(f.root, "authority-shadow", "outbox", "goal-a", "todos", `0000000001-${entry.entry_id}.prepared.json`);
  const prepared = JSON.parse(await readFile(path, "utf8"));
  prepared.source.previous_partition_digest = (entry.source as JsonObject).previous_partition_digest;
  const raw = JSON.stringify(prepared); await writeFile(path, raw); entry.prepared_sha256 = sha(raw);
  const result = await commitLocalAuthorityShadowEntry(request);
  assert.equal(result.reason_code, "source_partition_continuity_unproved");
  assert.equal((await f.store.loadAuthority() as { cursor: string }).cursor, "1");
});

test("Todo continuity ignores only a changed resume evaluation observation clock", async (t) => {
  const f = await fixture(t);
  const original = todo();
  original.resume_condition = {
    evaluated_at: "2026-09-20T00:00:00Z",
    satisfied: false,
    availability_reason: "resume_condition_pending",
  };
  const first = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [original] });
  const delivered = await commitLocalAuthorityShadowEntry(first);
  assert.equal(delivered.outcome, "delivered");
  await settleFiles(f, first, delivered);

  const reread = structuredClone(original);
  (reread.resume_condition as JsonObject).evaluated_at = "2026-09-21T00:00:00Z";
  const next = structuredClone(reread);
  next.text = "Durable Todo mutation after another read";
  const second = await pendingEntry(
    f,
    2,
    { handoff_mode: "hard_lease", todos: [next] },
    {
      previousPartitionProjection: { handoff_mode: "hard_lease", todos: [reread] },
    },
  );
  assert.equal((await commitLocalAuthorityShadowEntry(second)).outcome, "delivered");
});

test("prose bytes may change only while the canonical previous partition remains proved", async (t) => {
  const f = await fixture(t);
  await writeFile(f.statePath, `${await readFile(f.statePath, "utf8")}\n## Notes\nProse only.\n`);
  const request = await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] });
  assert.equal((await commitLocalAuthorityShadowEntry(request)).outcome, "delivered");
});

test("Python and TypeScript entry identity include the same root and lineage", async () => {
  const source = sha("source"); const root = sha("root");
  const script = "from loopx.control_plane.coordination.local_authority_shadow_outbox import entry_identity\nprint(entry_identity(goal_id='goal-a',partition='leases',seq=7,source_ref='" + source + "',capture_lineage_id='lineage-a',source_root_digest='" + root + "'))";
  const result = await execFileAsync(PYTHON, ["-c", script],
    { cwd: join(import.meta.dirname, "..", "..") });
  assert.equal(result.stdout.trim(), outboxEntryIdentity("goal-a", "leases", 7, source, "lineage-a", root));
  assert.notEqual(outboxEntryIdentity("goal-a", "leases", 7, source, "lineage-a", root),
    outboxEntryIdentity("goal-a", "leases", 7, source, "lineage-b", root));
});

test("Python and TypeScript share the stable Todo partition digest", async () => {
  const projection = {
    handoff_mode: "hard_lease",
    todos: [{
      ...todo(),
      resume_condition: {
        evaluated_at: "2026-09-21T00:00:00Z",
        satisfied: false,
        availability_reason: "resume_condition_pending",
      },
    }],
  };
  const script = [
    "import json, sys",
    "from loopx.control_plane.coordination.local_authority_shadow_projection import partition_digest",
    "print(partition_digest(json.loads(sys.argv[1])))",
  ].join("\n");
  const result = await execFileAsync(
    PYTHON,
    ["-c", script, JSON.stringify(projection)],
    { cwd: join(import.meta.dirname, "..", "..") },
  );
  assert.equal(result.stdout.trim(), localAuthorityShadowPartitionDigest("todos", projection));
});
