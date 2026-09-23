import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {readLocalCoordinationOperationReceipt} from "../../loopx/control_plane/coordination/local_authority_read.ts";

test("exact local receipt readback uses the selected provider without replaying an operation", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-operation-receipt-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const committed = await store.commitAuthority({expected_provider_revision: null,
    operation_id: "terminal:fixture", events: [], next_projection: {goal_id: "goal-a"},
    receipts: [{schema_version: "fixture_terminal_receipt_v0", operation_id: "terminal:fixture"}]});
  assert.equal(committed.status, "applied");

  const request = {schema_version: "loopx_local_coordination_operation_receipt_request_v0",
    runtime_root: root, goal_id: "goal-a", operation_id: "terminal:fixture"};
  const found = await readLocalCoordinationOperationReceipt(request);
  assert.equal(found.status, "found");
  assert.equal(found.source_authority, "file_v0");
  assert.deepEqual(found.receipts, [{schema_version: "fixture_terminal_receipt_v0",
    operation_id: "terminal:fixture"}]);
  assert.equal(found.provider_revision, committed.provider_revision);
  const missing = await readLocalCoordinationOperationReceipt({...request, operation_id: "terminal:other"});
  assert.equal(missing.status, "missing");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status === "loaded") assert.equal(after.provider_revision, committed.provider_revision);
});
