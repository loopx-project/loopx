import assert from "node:assert/strict";
import { mkdir, readFile, readdir, rm, writeFile, unlink, symlink } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import * as schemas from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import { commitLocalAuthorityShadowEntry, readLocalAuthorityShadow } from "../../loopx/control_plane/coordination/local_authority_shadow.ts";
import { composeLocalAuthorityShadowHead } from "../../loopx/control_plane/coordination/local_authority_shadow.ts";
import { bootstrapCoordinationRuntimeShadow, commitCoordinationRuntimeShadow, inspectCoordinationRuntimeShadow,
  qualifyCoordinationRuntimeShadow, readCoordinationRuntimeShadowTodoCandidate, rollbackCoordinationRuntimeShadow } from "../../loopx/control_plane/coordination/runtime_shadow.ts";
import { fixture, pendingEntry, projection, settleFiles, sourceRequest, todo, type ShadowFixture } from "./shadow_file_fixture.ts";

async function qualifiedFixture(t: test.TestContext): Promise<{ f: ShadowFixture; head: JsonObject }> {
  const f = await fixture(t); let head = projection();
  for (let seq = 1; seq <= 3; seq++) {
    const records = Array.from({ length: seq }, (_, index) => todo(`todo_${index + 1}`));
    head = projection(records);
    const entry = await pendingEntry(f, seq, { handoff_mode: "hard_lease", todos: records });
    const result = await commitLocalAuthorityShadowEntry(entry);
    assert.equal(result.outcome, "delivered"); await settleFiles(f, entry, result);
  }
  return { f, head };
}
async function qualify(f: ShadowFixture, head: JsonObject, extra: JsonObject = {}): Promise<JsonObject> {
  return await qualifyCoordinationRuntimeShadow({ ...await sourceRequest(f, head),
    schema_version: schemas.COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA,
    minimum_operations: 3, required_event_kinds: ["todo_add"], ...extra });
}

test("bootstrap is exactly replayable, has no mutation receipt and cannot overwrite a different baseline", async (t) => {
  const f = await fixture(t);
  const request = { ...await sourceRequest(f, f.baseline), schema_version: schemas.COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    operation_id: "bootstrap:test:first", source_version: "source:initial" };
  const replay = await bootstrapCoordinationRuntimeShadow(request);
  assert.equal(replay.status, "replayed"); assert.equal(replay.bootstrap_receipts_empty, true);
  const changed = await sourceRequest(f, projection([todo()]));
  const rejected = await bootstrapCoordinationRuntimeShadow({ ...request, ...changed });
  assert.equal(rejected.status, "failed");
  const history = await f.store.scanCommitted(null, 10); assert.equal(history.status, "page");
  if (history.status === "page") { assert.equal(history.transactions.length, 1); assert.deepEqual(history.transactions[0]?.receipts, []); }
});

for (const kind of ["unsupported_name", "file_symlink", "directory_symlink", "parent_symlink", "non_file"] as const) {
  test(`source admission rejects ${kind} without changing the candidate`, async (t) => {
    const f = await fixture(t);
    const before = await f.store.loadAuthority();
    let source = await sourceRequest(f, f.baseline);
    const directory = join(f.root, "goals", "goal-a", "task-leases");
    await mkdir(directory, {recursive: true});
    const outside = join(f.root, "retained-source");
    await mkdir(outside);
    const record = JSON.stringify({goal_id: "goal-a", todo_id: "todo_retained", status: "released", version: 4, lease_epoch: 2});
    await writeFile(join(outside, "todo_retained.json"), record);
    if (kind === "unsupported_name") await writeFile(join(directory, "unexpected lease.json"), record);
    if (kind === "file_symlink") await symlink(join(outside, "todo_retained.json"), join(directory, "todo_retained.json"));
    if (kind === "non_file") await mkdir(join(directory, "todo_retained.json"));
    if (kind === "directory_symlink") {
      await rm(directory, {recursive: true});
      await symlink(outside, directory);
    }
    if (kind === "parent_symlink") {
      const goalDirectory = join(f.root, "goals", "goal-a");
      await rm(goalDirectory, {recursive: true});
      await symlink(outside, goalDirectory);
    }
    if (kind !== "non_file") source = await sourceRequest(f, f.baseline);
    const result = await inspectCoordinationRuntimeShadow({...source,
      schema_version: schemas.COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA});
    assert.equal(result.status, "failed", JSON.stringify(result));
    assert.equal(result.reason_code, "source_lease_inventory_invalid");
    assert.deepEqual(await f.store.loadAuthority(), before);
    assert.equal(await readFile(join(outside, "todo_retained.json"), "utf8"), record);
    assert.deepEqual(await readdir(outside), ["todo_retained.json"]);
  });
}

test("regular orphan lease history stays witnessed without becoming a live lease", async (t) => {
  const f = await fixture(t);
  const directory = join(f.root, "goals", "goal-a", "task-leases");
  const record = JSON.stringify({goal_id: "goal-a", todo_id: "todo_retained", status: "released", version: 4, lease_epoch: 2,
    future_extension: {flag: false, missing_value: null}});
  await writeFile(join(directory, "todo_retained.json"), record);
  await writeFile(join(directory, ".task-leases.lock"), "retained host lock");
  const source = await sourceRequest(f, f.baseline);
  assert.equal(((source.source_snapshot as JsonObject).lease_inventory as JsonObject[]).length, 1);
  const result = await inspectCoordinationRuntimeShadow({...source,
    schema_version: schemas.COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA});
  assert.equal(result.status, "matched", JSON.stringify(result));
  assert.deepEqual(f.baseline.leases, []);
  assert.equal(await readFile(join(directory, "todo_retained.json"), "utf8"), record);
});

test("source validation cannot repair invalid UTF-8 into a different retained record", async (t) => {
  const f = await fixture(t);
  const path = join(f.root, "goals", "goal-a", "task-leases", "todo_retained.json");
  const bytes = Buffer.concat([Buffer.from('{"goal_id":"goal-a","todo_id":"todo_retained","note":"'),
    Buffer.from([0xff]), Buffer.from('"}')]);
  await writeFile(path, bytes);
  const before = await f.store.loadAuthority();
  const source = await sourceRequest(f, f.baseline);
  const result = await inspectCoordinationRuntimeShadow({...source,
    schema_version: schemas.COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA});
  assert.equal(result.status, "failed", JSON.stringify(result));
  assert.equal(result.qualified, false);
  assert.deepEqual(await f.store.loadAuthority(), before);
  assert.deepEqual(await readFile(path), bytes);
});

test("outbox qualification verifies bounded history coverage and never claims sustained parity", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const result = await qualify(f, head);
  assert.equal(result.status, "qualified"); assert.equal(result.qualified, true);
  assert.equal(result.scope, "bounded"); assert.equal(result.sustained_parity_verified, false);
  assert.equal(result.sustained_parity_verdict, "not_evaluated");
  assert.equal(result.decision_read_from_shadow, false);
  assert.equal((result.evidence as JsonObject).operation_count, 3);
  assert.equal((await qualify(f, head, { minimum_operations: 4 })).status, "insufficient_evidence");
  assert.equal((await qualify(f, head, { required_event_kinds: ["task_lease_acquire"] })).status, "insufficient_evidence");
});

test("handoff mode and complete Todo fields participate in the common semantic digest", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const changed = structuredClone(head); changed.handoff_mode = "soft_claim";
  assert.equal((await qualify(f, changed)).status, "drifted");
  const source = structuredClone(head);
  (source.todos as JsonObject[])[0]!.source_section = "User Todo";
  (source.todo_read_model as JsonObject).records_sha256 = canonicalAuthoritySha256(source.todos);
  assert.equal((await qualify(f, source)).status, "drifted");
});

test("pending entries and malformed cursor block eligibility without destroying evidence", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const cursorPath = join(f.root, "authority-shadow", "outbox", "goal-a", "todos", "drain-cursor.json");
  const original = await readFile(cursorPath);
  const bad = JSON.parse(original.toString()); bad.last_seq = true;
  await writeFile(cursorPath, JSON.stringify(bad));
  assert.equal((await qualify(f, head)).qualified, false);
  assert.equal(JSON.parse(await readFile(cursorPath, "utf8")).last_seq, true);
  await writeFile(cursorPath, original);
  const pending = await pendingEntry(f, 4, { handoff_mode: "hard_lease", todos: head.todos }, { marker: false });
  const result = await qualify(f, head);
  assert.equal(result.status, "not_ready"); assert.equal(result.qualified, false);
  const proof = await readLocalAuthorityShadow({ schema_version: schemas.LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    runtime_root: f.root, goal_id: "goal-a", receipt_operation_id: (pending.entry as JsonObject).entry_id, scan_limit: 10000 });
  assert.equal(proof.status, "loaded"); assert.equal((proof.proof as JsonObject).receipt, null);
});

test("qualification preserves unrecognized residue, symlink partitions and invalid UTF-8 cursors as ineligible", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const directory = join(f.root, "authority-shadow", "outbox", "goal-a", "todos");
  const residue = join(directory, ".tmp-unfinished");
  await writeFile(residue, "half a durable write");
  assert.equal((await qualify(f, head)).qualified, false);
  assert.equal(await readFile(residue, "utf8"), "half a durable write");
  await unlink(residue);
  const cursorPath = join(directory, "drain-cursor.json");
  const original = await readFile(cursorPath);
  const cursor = JSON.parse(original.toString());
  cursor.last_provider_revision = "replacement-\ufffd";
  const invalid = Buffer.from(JSON.stringify(cursor));
  const offset = invalid.indexOf(Buffer.from("\ufffd"));
  await writeFile(cursorPath, Buffer.concat([invalid.subarray(0, offset), Buffer.from([0xff]), invalid.subarray(offset + 3)]));
  assert.equal((await qualify(f, head)).reason_code, "outbox_file_invalid");
  await writeFile(cursorPath, original);
  await symlink(directory, join(f.root, "authority-shadow", "outbox", "goal-a", "leases"));
  assert.equal((await qualify(f, head)).qualified, false);
});

test("qualification requires the active outbox manifest to match its exact capture binding", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const path = join(f.root, "authority-shadow", "outbox", "goal-a", "manifest.json");
  const original = await readFile(path);
  await unlink(path);
  assert.equal((await qualify(f, head)).qualified, false);
  assert.equal((await readLocalAuthorityShadow({ schema_version: schemas.LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    runtime_root: f.root, goal_id: "goal-a", scan_limit: 10000 })).status, "loaded");
  const foreign = JSON.parse(original.toString()); foreign.capture_lineage_id = "foreign-manifest";
  await writeFile(path, JSON.stringify(foreign));
  assert.equal((await qualify(f, head)).qualified, false);
  assert.equal(JSON.parse(await readFile(path, "utf8")).capture_lineage_id, "foreign-manifest");
  await writeFile(path, original);
  assert.equal((await qualify(f, head)).qualified, true);
});

test("an observation transaction mixed into the candidate invalidates both qualification and read-candidate", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const loaded = await f.store.loadAuthority(); assert.equal(loaded.status, "loaded"); if (loaded.status !== "loaded") return;
  await f.store.commitAuthority({ expected_provider_revision: loaded.provider_revision, operation_id: "foreign-mirror",
    events: [{ schema_version: "loopx_coordination_runtime_shadow_event_v0" }],
    next_projection: loaded.head, receipts: [{ schema_version: "loopx_coordination_runtime_shadow_receipt_v0" }] });
  assert.equal((await qualify(f, head)).qualified, false);
  const read = await readCoordinationRuntimeShadowTodoCandidate({ ...await sourceRequest(f, head),
    schema_version: schemas.COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA, todo_id: "todo_1" });
  assert.equal(read.read_candidate_qualified, false);
});

test("source snapshot drift prevents inspection even when the supplied projection matches", async (t) => {
  const f = await fixture(t);
  const request = { ...await sourceRequest(f, f.baseline), schema_version: schemas.COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA };
  await writeFile(f.statePath, "external edit after source read\n");
  const result = await inspectCoordinationRuntimeShadow(request);
  assert.equal(result.status, "failed"); assert.equal(result.reason_code, "source_changed_retry");
  assert.equal((await f.store.loadAuthority() as { cursor: string }).cursor, "1");
});

test("read-candidate requires the same bounded eligibility before returning a Todo", async (t) => {
  const { f, head } = await qualifiedFixture(t);
  const request = { ...await sourceRequest(f, head), schema_version: schemas.COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA, todo_id: "todo_1" };
  const result = await readCoordinationRuntimeShadowTodoCandidate(request);
  assert.equal(result.status, "matched"); assert.equal(result.read_candidate_qualified, true);
  assert.equal(result.scope, "bounded"); assert.equal(result.decision_read_from_shadow, false);
  assert.deepEqual(result.todo, todo("todo_1"));
});

test("legacy mirror writes remain retired and cannot alter a new profile", async (t) => {
  const f = await fixture(t);
  const before = await f.store.loadAuthority();
  const result = await commitCoordinationRuntimeShadow({ runtime_root: f.root, goal_id: "goal-a" });
  assert.equal(result.reason_code, "legacy_lineage_read_only");
  assert.deepEqual(await f.store.loadAuthority(), before);
});

test("rollback can archive invalid cursor and pending entries without reading or editing primary content", async (t) => {
  const f = await fixture(t);
  await pendingEntry(f, 1, { handoff_mode: "hard_lease", todos: [todo()] }, { marker: false });
  const primary = await readFile(f.statePath);
  const loaded = await f.store.loadAuthority(); assert.equal(loaded.status, "loaded"); if (loaded.status !== "loaded") return;
  await writeFile(join(f.root, "authority-shadow", "outbox", "goal-a", "todos", "drain-cursor.json"), "invalid cursor");
  const result = await rollbackCoordinationRuntimeShadow({
    schema_version: schemas.COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA, runtime_root: f.root, goal_id: "goal-a",
    projection: {}, source_snapshot: { state_path: f.statePath }, operation_id: "rollback:test",
    expected_provider_revision: loaded.provider_revision, expected_bootstrap_operation_id: null });
  assert.equal(result.status, "applied"); assert.deepEqual(await readFile(f.statePath), primary);
  assert.equal((await f.store.loadAuthority()).status, "missing");
});

test("archiving a Todo drops its retained lease from the candidate head", async (t) => {
  const f = await fixture(t);
  const lease = { schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "todo_one", owner: "agent-a",
    idempotency_key: "k1", version: 1, lease_epoch: 1, status: "released", updated_at: "2026-09-06T00:00:00Z" };
  const base = { ...projection([todo("todo_one")]), leases: [lease] };
  // The published Todo partition retains the archived row for audit; the graph
  // it represents no longer contains `todo_one`. The fold must key off
  // `archive_state === "active"` like the source projection does, or the
  // retained archived row re-admits the lease its archive just orphaned.
  const archivedTodo = { ...todo("todo_one", "done"), archive_state: "archive" };
  const archived = composeLocalAuthorityShadowHead(base, "goal-a",
    { partition: "todos", seq: 2 }, projection([archivedTodo]), "sha256:archived");
  assert.deepEqual(archived.todos, [archivedTodo]);
  assert.deepEqual(archived.leases, []);
  // A lease whose Todo is still in the graph survives the same fold.
  const retained = composeLocalAuthorityShadowHead(base, "goal-a",
    { partition: "todos", seq: 2 }, projection([todo("todo_one")]), "sha256:retained");
  assert.deepEqual(retained.leases, [lease]);
  // The lease partition remains authoritative for its own writes.
  const leased = composeLocalAuthorityShadowHead(base, "goal-a",
    { partition: "leases", seq: 3 }, { leases: [] }, "sha256:leased");
  assert.deepEqual(leased.leases, []);
  assert.deepEqual(leased.todos, [todo("todo_one")]);
});
