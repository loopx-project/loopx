import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {
  TODO_CANONICAL_READ_RECORD_FIELDS,
  TODO_CANONICAL_READ_RECORD_SCHEMA,
  prepareCoordinationProjectionCommit,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {
  acknowledgeLocalCoordinationTodoArchive,
  archiveLocalCoordinationTodos,
  LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import { shadowManagementDirectory } from "../../loopx/control_plane/coordination/shadow_management.ts";
import { executeCoordinationTodoArchiveCompleted } from "../../loopx/control_plane/coordination/todo_archive.ts";
import { engageFence, promotionRequest } from "./local_promotion_fixture.ts";

function todo(id: string): JsonObject {
  return {schema_version: "todo_item_v0", todo_id: id, role: "agent",
    status: "done", done: true, text: "Completed archive fixture",
    archive_state: "active", source_section: "Agent Todo"};
}

async function fixture(t: test.TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-archive-attempt-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const todos = [todo("done-a"), todo("done-b")];
  const projection = {goal_id: "goal-a", handoff_mode: "soft_claim", todos, leases: [],
    todo_read_model: {schema_version: TODO_CANONICAL_READ_RECORD_SCHEMA,
      todo_count: todos.length, records_sha256: canonicalAuthoritySha256(todos),
      contract_fields: [...TODO_CANONICAL_READ_RECORD_FIELDS]}};
  const seed = await store.commitAuthority({
    operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
    next_projection: projection,
  });
  assert.equal(seed.status, "applied");
  if (seed.status !== "applied") throw new Error("fixture initialization failed");
  await writeFile(join(root, "ACTIVE_GOAL_STATE.md"), "# Synthetic archive source\n");
  await engageFence(promotionRequest(root, projection, seed.provider_revision));
  const request = {schema_version: LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA,
    runtime_root: root, goal_id: "goal-a", role: "agent", max_active_done: 0,
    operation_id: "archive-a", expected_provider_revision: seed.provider_revision,
    dry_run: false, observed_at: "2026-09-08T01:00:00Z"};
  const ack = (operationId: string) => acknowledgeLocalCoordinationTodoArchive({
    schema_version: LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA,
    runtime_root: root, goal_id: "goal-a", role: "agent", operation_id: operationId,
  });
  const slotPath = join(shadowManagementDirectory(root, "goal-a"), "archive-agent.json");
  return {root, store, request, ack, slotPath};
}

async function addCompleted(store: FileAuthorityStore, id: string) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("fixture authority missing");
  const added = await store.commitAuthority(prepareCoordinationProjectionCommit({
    goal_id: "goal-a", operation_id: `add-${id}`,
    expected_provider_revision: head.provider_revision, projection: head.head,
    mutations: [{kind: "todo_upsert", todo: todo(id)}],
  }));
  assert.equal(added.status, "applied");
  if (added.status !== "applied") throw new Error("fixture update failed");
  return added.provider_revision;
}

test("archive retries recover the accepted batch until exact projection acknowledgement", async (t) => {
  const {store, request, ack, slotPath} = await fixture(t);
  const applied = await archiveLocalCoordinationTodos(request);
  assert.equal(applied.status, "applied", JSON.stringify(applied));
  assert.deepEqual(applied.moved_todo_ids, ["done-a", "done-b"]);
  const retained = await readFile(slotPath, "utf8");
  // The caller loses the result before projection. Later unrelated work must
  // not change either the retried selection or the original receipt.
  const changedRevision = await addCompleted(store, "done-c");
  const retryRequest = {...request, operation_id: "archive-new-suggestion",
    expected_provider_revision: changedRevision};
  const replay = await archiveLocalCoordinationTodos(retryRequest);
  assert.equal(replay.status, "replayed", JSON.stringify(replay));
  assert.equal(replay.operation_id, "archive-a");
  assert.equal(replay.changed, false);
  assert.equal(replay.moved_count, 2);
  assert.deepEqual(replay.original_receipt, applied.original_receipt);
  assert.equal(await readFile(slotPath, "utf8"), retained);
  assert.equal((await archiveLocalCoordinationTodos({...retryRequest,
    max_active_done: 1})).conflict_kind, "archive_attempt_pending");
  const beforeAck = await store.loadAuthority();
  assert.equal((await ack("archive-old")).status, "stale");
  assert.equal(await readFile(slotPath, "utf8"), retained);
  assert.equal((await ack("archive-a")).status, "acknowledged");
  assert.deepEqual(await store.loadAuthority(), beforeAck, "ACK cannot change domain state");

  // A successfully delivered attempt ends. This must kill an implementation
  // that permanently hashes only goal/role/limit and replays the first batch.
  const next = await archiveLocalCoordinationTodos(retryRequest);
  assert.equal(next.status, "applied", JSON.stringify(next));
  assert.deepEqual(next.moved_todo_ids, ["done-c"]);
  const nextSlot = await readFile(slotPath, "utf8");
  assert.equal((await ack("archive-a")).status, "stale");
  assert.equal(await readFile(slotPath, "utf8"), nextSlot);
  assert.equal((await ack("archive-new-suggestion")).status, "acknowledged");
  assert.equal((await ack("archive-new-suggestion")).status, "no_change");
});

test("archive snapshot drift is rejected before selection and releases the uncommitted attempt", async (t) => {
  const {store, request, slotPath} = await fixture(t);
  const revision = await addCompleted(store, "done-c");
  const before = await store.loadAuthority();
  const rejected = await archiveLocalCoordinationTodos(request);
  assert.equal(rejected.status, "conflict", JSON.stringify(rejected));
  assert.equal(rejected.conflict_kind, "provider_revision_mismatch");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
  assert.equal(JSON.parse(await readFile(slotPath, "utf8")).attempt, null);
  const fresh = await archiveLocalCoordinationTodos({...request,
    expected_provider_revision: revision, operation_id: "archive-fresh"});
  assert.equal(fresh.status, "applied");
  assert.equal(fresh.moved_count, 3);
});

test("archive previews neither create nor acknowledge attempts; no-change retains zero-write authority", async (t) => {
  const {store, request, slotPath, ack} = await fixture(t);
  const before = await store.loadAuthority();
  assert.equal((await archiveLocalCoordinationTodos({...request, dry_run: true})).status, "planned");
  await assert.rejects(readFile(slotPath), {code: "ENOENT"});
  assert.deepEqual(await store.loadAuthority(), before);
  const applied = await archiveLocalCoordinationTodos(request);
  const pendingBytes = await readFile(slotPath, "utf8");
  const preview = await archiveLocalCoordinationTodos({...request, dry_run: true,
    expected_provider_revision: applied.provider_revision});
  assert.equal(preview.status, "no_change");
  assert.equal(await readFile(slotPath, "utf8"), pendingBytes);
  assert.equal((await ack(request.operation_id)).status, "acknowledged");
  const archived = await store.loadAuthority();
  const noChange = await archiveLocalCoordinationTodos({...request,
    operation_id: "archive-empty", expected_provider_revision: applied.provider_revision});
  assert.equal(noChange.status, "no_change");
  assert.deepEqual(await store.loadAuthority(), archived);
  assert.equal((await store.readReceipt("archive-empty")).status, "missing");
  assert.equal(JSON.parse(await readFile(slotPath, "utf8")).attempt, null);
});

test("concurrent same-intent archive calls share one durable receipt", async (t) => {
  const {request, store} = await fixture(t);
  const results = await Promise.all([request, {...request, operation_id: "contender"}]
    .map((input) => archiveLocalCoordinationTodos(input)));
  assert.equal(results.filter((result) => result.status === "applied").length, 1);
  assert.equal(results.filter((result) => result.status === "replayed").length, 1);
  assert.deepEqual(results[0].original_receipt, results[1].original_receipt);
  const scan = await store.scanCommitted(null, 10);
  assert.equal(scan.status, "page");
  if (scan.status === "page") assert.equal(scan.transactions.length, 2);
});

test("archive rejects malformed retry metadata and preview types before effectful dispatch", async (t) => {
  const {request, store, slotPath} = await fixture(t);
  for (const dryRun of ["true", 1, null]) {
    let opened = false;
    const result = await archiveLocalCoordinationTodos({...request, dry_run: dryRun}, {
      createStore() {opened = true; return store;},
    });
    assert.equal(result.status, "failed");
    assert.equal(opened, false);
  }
  const applied = await archiveLocalCoordinationTodos(request);
  assert.equal(applied.status, "applied");
  const original = await readFile(slotPath, "utf8");
  const slot = JSON.parse(original);
  slot.attempt.store_identity = "foreign-store";
  await writeFile(slotPath, JSON.stringify(slot));
  const before = await store.loadAuthority();
  const failure = await archiveLocalCoordinationTodos(request);
  assert.equal(failure.status, "failed");
  assert.match(String(failure.reason), /different authority store/);
  assert.deepEqual(await store.loadAuthority(), before);
});

test("pre-binding v0 archive receipts retain their identity across local upgrade and acknowledgement", async (t) => {
  const {request, store, ack} = await fixture(t);
  const old = await executeCoordinationTodoArchiveCompleted(store, {
    goal_id: request.goal_id, role: "agent", max_active_done: 0,
    operation_id: "archive-v0", dry_run: false, now: new Date(request.observed_at),
  });
  assert.equal(old.status, "applied");
  const replay = await archiveLocalCoordinationTodos({...request,
    operation_id: "archive-v0", expected_provider_revision: undefined});
  assert.equal(replay.status, "replayed", JSON.stringify(replay));
  assert.deepEqual(replay.original_receipt, old.original_receipt);
  assert.equal((await ack("archive-v0")).status, "acknowledged");
  const revision = await addCompleted(store, "done-c");
  // Explicitly changing the old request's head binding remains an identity
  // conflict, but that rejected attempt cannot block a subsequent new batch.
  const mismatch = await archiveLocalCoordinationTodos({...request,
    operation_id: "archive-v0", expected_provider_revision: revision});
  assert.equal(mismatch.reason_code, "coordination_operation_identity_mismatch");
  const fresh = await archiveLocalCoordinationTodos({...request,
    operation_id: "archive-after-upgrade", expected_provider_revision: revision});
  assert.equal(fresh.status, "applied", JSON.stringify(fresh));
  assert.deepEqual(fresh.moved_todo_ids, ["done-c"]);
});

test("archive retains an applied attempt when its receipt is temporarily invisible", async (t) => {
  const {request, store, slotPath, ack} = await fixture(t);
  const readReceipt = store.readReceipt.bind(store);
  let hideCommittedReceipt = true;
  store.readReceipt = async (operationId) => {
    const receipt = await readReceipt(operationId);
    return hideCommittedReceipt && operationId === request.operation_id && receipt.status === "found"
      ? {status: "missing"}
      : receipt;
  };
  const dependencies = {createStore: () => store};

  const interrupted = await archiveLocalCoordinationTodos(request, dependencies);
  assert.equal(interrupted.status, "failed", JSON.stringify(interrupted));
  assert.equal(interrupted.reason_code, "coordination_commit_readback_mismatch");
  const committed = await store.loadAuthority();
  assert.equal(committed.status, "loaded");
  if (committed.status !== "loaded") throw new Error("archive commit was not durable");
  const durableReceipt = await readReceipt(request.operation_id);
  assert.equal(durableReceipt.status, "found");
  if (durableReceipt.status !== "found") throw new Error("archive receipt was not durable");
  const retained = await readFile(slotPath, "utf8");
  assert.equal(JSON.parse(retained).attempt.operation_id, request.operation_id);

  hideCommittedReceipt = false;
  const replay = await archiveLocalCoordinationTodos({...request,
    operation_id: "new-suggestion-after-readback-failure",
    expected_provider_revision: committed.provider_revision,
  }, dependencies);
  assert.equal(replay.status, "replayed", JSON.stringify(replay));
  assert.equal(replay.operation_id, request.operation_id);
  assert.equal(replay.moved_count, 2);
  assert.deepEqual(replay.original_receipt, durableReceipt.receipts[0]);
  assert.deepEqual(await store.loadAuthority(), committed, "retry cannot commit a second archive");
  assert.equal(await readFile(slotPath, "utf8"), retained);
  assert.equal((await ack(request.operation_id)).status, "acknowledged");
});
