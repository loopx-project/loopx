import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, realpath, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {cancelSourcePreparation} from "../../loopx/control_plane/coordination/planned_transfer_authority.ts";
import {legacyCoordinationWriterFencePath, loadLegacyCoordinationWriterFence, NEW_GOAL_WRITER_FENCE_SCHEMA,
  PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, restorePlannedTransferWriterFenceUnderLock,
  type PlannedTransferWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";

test("cancellation fence CAS restores only the exact prepared source to its original normal owner", async t => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-transfer-cancel-")));
  t.after(() => rm(root, {recursive: true, force: true}));
  const goal = "report", path = legacyCoordinationWriterFencePath(root, goal);
  await mkdir(dirname(path), {recursive: true});
  // This unit check qualifies only the fence CAS. The real registered origin,
  // creation receipt, provider and context proof are exercised through Python.
  const prior = {schema_version: NEW_GOAL_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
    fence_id: "original-create", creation_operation_id: "original-create", creation_identity_sha256: "a".repeat(64), creation_completed: true};
  const prepared: PlannedTransferWriterFence = {schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA,
    state: "engaged", goal_id: goal, role: "source", fence_id: "planned-transfer:move-a", operation_id: "move-a",
    source_identity_sha256: canonicalAuthoritySha256({original: "preparation"})};
  for (const observed of [{...prepared, role: "target"}, {...prepared, operation_id: "later-move"}, {...prior, fence_id: "later-create"}]) {
    await writeFile(path, JSON.stringify(observed));
    const bytes = await readFile(path);
    await assert.rejects(restorePlannedTransferWriterFenceUnderLock(root, goal, prepared, prior), /writer fence changed/);
    assert.deepEqual(await readFile(path), bytes);
  }
  await writeFile(path, JSON.stringify(prepared));
  for (const invalid of [{...prior, creation_completed: false}, {...prior, goal_id: "other"}, {...prior, extra: true}]) {
    await assert.rejects(restorePlannedTransferWriterFenceUnderLock(root, goal, prepared, invalid));
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), prepared);
  }
  await restorePlannedTransferWriterFenceUnderLock(root, goal, prepared, prior);
  assert.deepEqual(await loadLegacyCoordinationWriterFence(root, goal), {status: "loaded", fence: prior});
  const bytes = await readFile(path);
  await restorePlannedTransferWriterFenceUnderLock(root, goal, prepared, prior);
  assert.deepEqual(await readFile(path), bytes, "exact idempotent recovery does not rewrite the fence");
});

test("native cancellation rejects unsupported execution or unknown preparation without creating a replacement", async t => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-transfer-cancel-input-")));
  t.after(() => rm(root, {recursive: true, force: true}));
  const request: JsonObject = {runtime_root: root, goal_id: "report", operation_id: "move-a", execute: true};
  for (const patch of [{execute: false}, {unexpected: true}, {source_closed: true}]) {
    await assert.rejects(cancelSourcePreparation({...request, ...patch}), /original address and explicit execution/);
  }
  await assert.rejects(cancelSourcePreparation(request), /retained original registered source preparation/);
  assert.deepEqual(await loadLegacyCoordinationWriterFence(root, "report"), {status: "missing"});
});
