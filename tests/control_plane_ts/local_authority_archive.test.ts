import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {manageLocalAuthorityArchive as manage} from "../../loopx/control_plane/coordination/local_authority_archive.ts";
import {exportAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";

const schema_version = "loopx_authority_archive_admin_request_v0";

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: completion observation is read-only historical evidence, not replay or current audit`, async () => {
    const root = await mkdtemp(join(tmpdir(), "archive-receipt-"));
    try {
      const source = new FileAuthorityStore(join(root, "source"), "goal");
      await source.commitAuthority({operation_id: "original", expected_provider_revision: null,
        next_projection: {goal_id: "goal", metadata: {value: null, enabled: false}}, events: [], receipts: [{accepted: true}]});
      const archive = join(root, "archive");
      const summary = await exportAuthorityArchive(source, "goal", archive);
      const destination = join(root, "destination");
      const request = {schema_version, action: "restore-receipt", goal_id: "goal", destination,
        provider, archive_sha256: summary.archive_sha256};
      const absent = await manage(request);
      assert.equal(absent.status, "receipt_missing");
      assert.equal(absent.binding_found, false);
      await assert.rejects(readFile(join(destination, "restore-binding.json")), {code: "ENOENT"});
      const restored = await manage({...request, action: "restore", archive, execute: true});
      assert.equal(restored.status, "restored");
      const before = await readFile(join(destination, "verified-restore.json"));
      const observed = await manage(request);
      assert.equal(observed.status, "receipt_found");
      assert.deepEqual(observed.receipt, restored.archive);
      assert.equal(observed.verification, "historical_receipt_only");
      assert.equal(observed.current_integrity_verified, false);
      assert.equal(observed.worker_liveness, "unknown");
      assert.equal(observed.authority_changed, false);
      assert.equal(observed.execution_authority_granted, false);
      const target = provider === "file" ? new FileAuthorityStore(join(destination, "store"), "goal")
        : new SqliteAuthorityStore(join(destination, "store"), "goal");
      const head = await target.loadAuthority();
      assert.equal(head.status, "loaded");
      if (head.status !== "loaded") throw new Error("missing restored head");
      await target.commitAuthority({operation_id: "later", expected_provider_revision: head.provider_revision,
        next_projection: {goal_id: "goal", later: true}, events: [], receipts: [{later: true}]});
      assert.deepEqual(await manage(request), observed, "later writes do not turn a historical receipt into current proof");
      const audit = await manage({...request, action: "audit", archive, allow_newer_head: false});
      assert.equal(audit.status, "failed", "the independent current audit still detects extra commits");
      await rm(archive);
      await rm(join(destination, "store"), {recursive: true});
      assert.deepEqual(await manage(request), observed, "observation does not open stores or read the archive");
      await assert.rejects(readFile(archive), {code: "ENOENT"});
      assert.deepEqual(await readFile(join(destination, "verified-restore.json")), before);
      for (const change of [{goal_id: "other"}, {provider: provider === "file" ? "sqlite" : "file"},
        {archive_sha256: "a".repeat(64)}, {provider: "postgresql"}, {archive_sha256: "invalid"}]) {
        assert.equal((await manage({...request, ...change})).status, "failed");
      }
      const receipt = JSON.parse(before.toString());
      for (const content of ["{", " ".repeat(64 * 1024 + 1), JSON.stringify({...receipt, goal_id: "other"}),
        JSON.stringify({...receipt, archive_sha256: "b".repeat(64)}), JSON.stringify({...receipt, commits: "0"}),
        JSON.stringify({...receipt, status: "planned"}), JSON.stringify({...receipt, projection_sha256: "bad"}),
        JSON.stringify({...receipt, target_store_identity: receipt.source_store_identity})]) {
        await writeFile(join(destination, "verified-restore.json"), content);
        assert.equal((await manage(request)).status, "failed", "invalid receipt never certifies completion");
      }
      await writeFile(join(destination, "verified-restore.json"), before);
      await rm(join(destination, "restore-binding.json"));
      assert.equal((await manage(request)).status, "failed", "an unbound receipt is not accepted");
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("a bound interrupted restore with no receipt does not claim failure or worker liveness", async () => {
  const root = await mkdtemp(join(tmpdir(), "archive-pending-"));
  try {
    const destination = join(root, "destination");
    await mkdir(destination);
    await writeFile(join(destination, "restore-binding.json"), JSON.stringify({
      schema_version: "loopx_authority_restore_destination_v0", goal_id: "goal", provider: "file", archive_sha256: "a".repeat(64)}));
    const result = await manage({schema_version, action: "restore-receipt", goal_id: "goal", provider: "file",
      archive_sha256: "a".repeat(64), destination});
    assert.equal(result.status, "receipt_missing");
    assert.equal(result.binding_found, true);
    assert.equal(result.worker_liveness, "unknown");
    await assert.rejects(readFile(join(destination, "verified-restore.json")), {code: "ENOENT"});
  } finally { await rm(root, {recursive: true, force: true}); }
});
