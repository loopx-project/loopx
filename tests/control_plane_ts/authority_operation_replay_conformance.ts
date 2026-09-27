/** Direct local-provider replay: full intent matches a verified historical commit.
 * This is not Goal-instance isolation or permission to execute another effect. */
import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {authorityStoreCommitFixture as commit} from "./authority_store_conformance.ts";

export function registerAuthorityOperationReplayConformance(
  provider: string, factory: AuthorityStoreConformanceFactory,
): void {
  test(`${provider}: historical operation replay preserves full intent and later state`, async t => {
    const {store, contender} = await factory(t);
    const input = commit(null, "original", 1, 1);
    input.next_projection.metadata = {labels: ["retained", "中"], nested: {value: 7}};
    const first = await store.commitAuthority(input);
    assert.equal(first.status, "applied"); if (first.status !== "applied") return;
    const second = await store.commitAuthority(commit(first.provider_revision, "later", 2, 2));
    assert.equal(second.status, "applied"); if (second.status !== "applied") return;
    const snapshot = async () => ({head: await contender.loadAuthority(),
      receipt: await contender.readReceipt("original"), history: await contender.scanCommitted(null, 10)});
    const before = await snapshot();
    for (const basis of [null, second.provider_revision]) {
      for (const change of ["none", "key_order", "projection", "events", "receipts"] as const) {
        const replay = structuredClone(input);
        replay.expected_provider_revision = basis;
        if (change === "key_order") replay.next_projection = Object.fromEntries(Object.entries(replay.next_projection).reverse());
        if (change === "projection") replay.next_projection.metadata = {labels: ["different"]};
        if (change === "events") replay.events = [{type: "different"}];
        if (change === "receipts") replay.receipts = [{result: "different"}];
        const result = await contender.commitAuthority(replay);
        if (change === "none" || change === "key_order") assert.deepEqual(result, first);
        else {
          assert.equal(result.status, "conflict", `${change}-only drift must be rejected`);
          if (result.status === "conflict") assert.equal(result.conflict_kind, "operation_id_exists");
        }
        assert.deepEqual(await snapshot(), before, `${change} with basis ${basis} changed committed state`);
      }
    }
  });

  for (const matching of [true, false]) {
    test(`${provider}: concurrent ${matching ? "matching" : "different"} intent never double commits`, async t => {
      const {store, contender} = await factory(t);
      const first = commit(null, "racing-operation", 1, 1);
      const second = structuredClone(first);
      if (!matching) second.next_projection.authority_revision = 2;
      const results = await Promise.all([store.commitAuthority(first), contender.commitAuthority(second)]);
      const applied = results.filter(result => result.status === "applied");
      assert.equal(applied.length, matching ? 2 : 1);
      if (matching) assert.deepEqual(results[0], results[1]);
      else {
        const rejected = results.find(result => result.status === "conflict");
        assert.equal(rejected?.conflict_kind, "operation_id_exists");
      }
      assert.deepEqual(await store.loadAuthority(), await contender.loadAuthority());
      const page = await contender.scanCommitted(null, 10);
      assert.equal(page.status, "page"); if (page.status !== "page") return;
      assert.equal(page.transactions.length, 1);
      const winner = results[0]!.status === "applied" ? first : second;
      assert.deepEqual(page.transactions[0]!.projection, winner.next_projection);
      assert.deepEqual(page.transactions[0]!.receipts, winner.receipts);
      assert.deepEqual(page.transactions[0]!.events, winner.events);
    });
  }
}
