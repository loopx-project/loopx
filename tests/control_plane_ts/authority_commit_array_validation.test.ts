import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";

for (const Provider of [FileAuthorityStore, SqliteAuthorityStore]) {
  for (const field of ["events", "receipts"] as const) {
    test(`${Provider.name} rejects holes in ${field} before persistence and permits a corrected retry`, async t => {
      const directory = await mkdtemp(join(tmpdir(), "authority-commit-arrays-"));
      t.after(() => rm(directory, {recursive: true, force: true}));
      const store = new Provider(directory, "goal");
      const empty = new Array<JsonObject>(1);
      const middle: JsonObject[] = [{kind: "first"}, {kind: "deleted"}, {kind: "last"}];
      delete middle[1];
      const trailing: JsonObject[] = [{kind: "first"}];
      trailing.length = 2;
      const masked = new Array<JsonObject>(1);
      masked[Symbol.iterator] = () => [{kind: "masked"}].values();
      const request = (revision: string | null, operation: string, items: JsonObject[]) => ({
        expected_provider_revision: revision, operation_id: operation,
        events: field === "events" ? items : [{kind: "update"}],
        receipts: field === "receipts" ? items : [{accepted: true}],
        next_projection: {state: operation},
      });
      const invalid = await store.commitAuthority(request(null, "initial", empty));
      assert.equal(invalid.status, "failed", JSON.stringify(invalid));
      if (invalid.status === "failed") assert.equal(invalid.reason_code, "invalid_commit_request");
      assert.deepEqual(await store.loadAuthority(), {status: "missing"});

      // Empty top-level lists and nested sparse JSON retain their existing contract.
      const seed = await store.commitAuthority({
        expected_provider_revision: null, operation_id: "seed",
        events: [], receipts: [{values: Array(2)}], next_projection: {state: "seed"},
      });
      assert.equal(seed.status, "applied", JSON.stringify(seed));
      if (seed.status !== "applied") throw new Error("seed failed");
      const before = await readFile(store.path);
      const head = await store.loadAuthority();
      for (const items of [empty, middle, trailing, masked]) {
        const result = await store.commitAuthority(request(seed.provider_revision, "corrected", items));
        assert.equal(result.status, "failed", JSON.stringify(result));
        if (result.status === "failed") assert.equal(result.reason_code, "invalid_commit_request");
        assert.deepEqual(await readFile(store.path), before, "rejected input must not alter authority bytes");
        assert.deepEqual(await store.loadAuthority(), head);
        assert.deepEqual(await store.readReceipt("corrected"), {status: "missing"});
      }
      const corrected = request(seed.provider_revision, "corrected", [{kind: "valid"}]);
      const applied = await store.commitAuthority(corrected);
      assert.equal(applied.status, "applied", JSON.stringify(applied));
      if (applied.status !== "applied") throw new Error("corrected commit failed");
      assert.equal(applied.cursor, "2", "rejected requests must not consume cursors");
      if (store instanceof FileAuthorityStore) {
        // Force full on-disk verification instead of reusing the writer's cache.
        await writeFile(store.path, (await readFile(store.path, "utf8")) + "\n");
      }
      const reader = new Provider(directory, "goal");
      const loaded = await reader.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status === "loaded") assert.deepEqual(loaded.head, {state: "corrected"});
      const history = await reader.scanCommitted(null, 10);
      assert.equal(history.status, "page");
      if (history.status === "page") {
        assert.deepEqual(history.transactions.map(row => row.operation_id), ["seed", "corrected"]);
        assert.deepEqual(history.transactions[0].receipts, [{values: [null, null]}]);
      }
      const receipt = await reader.readReceipt("corrected");
      assert.equal(receipt.status, "found");
      if (receipt.status === "found") assert.deepEqual(receipt.receipts, corrected.receipts);
      assert.deepEqual(await reader.commitAuthority(corrected), applied);
    });
  }
}
