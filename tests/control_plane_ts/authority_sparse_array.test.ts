import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {applyAuthorityStateDelta, authorityStateDelta} from "../../loopx/control_plane/coordination/authority_state_log.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";

test("array deltas compare holes as persisted nulls and retain explicit undefined rejection", () => {
  const cases: [unknown[], unknown[], unknown[]][] = [
    [Array(2), ["done", null], ["done", null]],
    [[null, "old", null], [null, "done", null], [null, "done", null]],
    [[1], Array(2), [null, null]],
    [[null], Array(2), [null, null]],
    [[null, "old"], Array(1), [null]],
    [[1, null], [1, , "done"], [1, null, "done"]],
  ];
  for (const [previous, next, expected] of cases) {
    const delta = authorityStateDelta({items: previous}, {items: next});
    const encoded = JSON.parse(JSON.stringify(delta));
    assert.deepEqual(JSON.parse(canonicalAuthorityBytes(
      applyAuthorityStateDelta({items: previous}, encoded)).toString("utf8")), {items: expected});
    assert.deepEqual(canonicalAuthorityBytes(applyAuthorityStateDelta({items: previous}, delta)),
      canonicalAuthorityBytes({items: expected}));
  }
  assert.deepEqual(authorityStateDelta({items: Array(2)}, {items: [null, null]}).operations, []);
  for (const items of [[undefined], [1, undefined]]) {
    assert.throws(() => authorityStateDelta({items: [0]}, {items}), /strict JSON/);
    assert.throws(() => authorityStateDelta({items}, {items: [0]}), /strict JSON/);
  }
});

for (const provider of ["file", "sqlite"]) {
  test(`${provider} commits sparse arrays, updates them and replays the original receipt`, async t => {
    const directory = await mkdtemp(join(tmpdir(), "authority-sparse-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const open = () => provider === "file"
      ? new FileAuthorityStore(directory, "goal")
      : new SqliteAuthorityStore(directory, "goal");
    const store = open();
    const projections: JsonObject[] = [
      {items: Array(2)},
      {items: ["done", null]},
      {items: [null, , "returned"]},
    ];
    let revision: string | null = null;
    const commits = [];
    const receipts = [];
    for (const [index, next_projection] of projections.entries()) {
      const commit = {expected_provider_revision: revision,
        operation_id: `array-${index}`, events: [{index}], receipts: [{index}], next_projection};
      const receipt = await store.commitAuthority(commit);
      assert.equal(receipt.status, "applied", JSON.stringify(receipt));
      if (receipt.status !== "applied") throw new Error("commit failed");
      revision = receipt.provider_revision;
      commits.push(commit);
      receipts.push(receipt);
      const loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") throw new Error("head missing");
      assert.deepEqual(canonicalAuthorityBytes(loaded.head), canonicalAuthorityBytes(next_projection));
    }
    assert.deepEqual(await store.commitAuthority(commits[0]!), receipts[0]);
    if (store instanceof FileAuthorityStore) {
      // Change bytes to force cold verification rather than reuse the writer cache.
      await writeFile(store.path, (await readFile(store.path, "utf8")) + "\n");
    }
    const reopened = open();
    const page = await reopened.scanCommitted(null, 10);
    assert.equal(page.status, "page");
    if (page.status !== "page") throw new Error("history missing");
    assert.deepEqual(page.transactions.map(row => row.projection), [
      {items: [null, null]}, {items: ["done", null]}, {items: [null, null, "returned"]},
    ]);
    assert.equal(page.has_more, false);
    assert.deepEqual(await reopened.commitAuthority(commits[0]!), receipts[0]);
    const found = await reopened.readReceipt("array-0");
    assert.equal(found.status, "found");
    if (found.status === "found") assert.deepEqual(found.receipts, [{index: 0}]);
  });
}
