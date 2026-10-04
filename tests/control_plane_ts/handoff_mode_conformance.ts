import {registerHandoffModeMigrationConformance} from "./handoff_mode_migration_conformance.ts";
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {executeHandoffModeSet} from "../../loopx/control_plane/coordination/handoff_mode_transaction.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

const request = {goal_id: "mode-goal", operation_id: "mode-change", requested_mode: "soft_claim",
  observed_at: "2026-09-13T00:00:00Z", dry_run: false};
async function head(store: AuthorityStore) {
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") throw new Error("missing fixture");
  return loaded;
}
function quiescent() {
  const projection = productionScaleCoordinationFixture(request.goal_id).projection;
  for (const todo of projection.todos as JsonObject[]) delete todo.claimed_by;
  for (const lease of projection.leases as JsonObject[]) lease.status = "released";
  (projection.todo_read_model as JsonObject).records_sha256 = canonicalAuthoritySha256(projection.todos);
  return projection;
}
async function seed(store: AuthorityStore, projection = quiescent()) {
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: projection, events: [], receipts: []})).status, "applied");
}
function intercept(store: AuthorityStore, commit: AuthorityStore["commitAuthority"]): AuthorityStore {
  return {storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
    readReceipt: id => store.readReceipt(id), scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
    commitAuthority: commit};
}

export function registerHandoffModeConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  registerHandoffModeMigrationConformance(provider, factory);
  test(`${provider}: retired legacy intent rejects new writes but preserves an original receipt replay`, async t => {
    const {store} = await factory(t);
    await seed(store);
    const original = {...request, operation_id: "historical-legacy", requested_mode: "legacy"};
    const before = await head(store);
    // A valid pre-retirement receipt is historical input, not produced by the
    // implementation under test. Recovery must never reinterpret it as a grant.
    const decision = {goal_id: original.goal_id, operation_id: original.operation_id,
      previous_mode: "hard_lease", previous_mode_valid: true, handoff_mode: "legacy", changed: true};
    assert.equal((await store.commitAuthority({operation_id: original.operation_id,
      expected_provider_revision: before.provider_revision, next_projection: {...before.head, handoff_mode: "legacy"},
      events: [{schema_version: "loopx_handoff_mode_changed_v0", ...decision}], receipts: [{
        schema_version: "loopx_coordination_handoff_mode_receipt_v0", goal_id: original.goal_id,
        operation_id: original.operation_id,
        request_sha256: canonicalAuthoritySha256({goal_id: original.goal_id, requested_mode: "legacy"}), decision}]})).status, "applied");
    const rejected = await executeHandoffModeSet(store, {...original, operation_id: "new-legacy"});
    assert.equal(rejected.reason_code, "handoff_mode_retired");
    assert.equal((await store.readReceipt("new-legacy")).status, "missing");
    assert.equal((await executeHandoffModeSet(store, {...request, operation_id: "new-hard", requested_mode: "hard_lease"})).status, "applied");
    const later = await head(store);
    const replay = await executeHandoffModeSet(store, original);
    assert.equal(replay.status, "replayed");
    assert.equal(replay.changed, false);
    assert.deepEqual(await head(store), later);
  });
  test(`${provider}: mode transition preserves the large projection and seals replay, including no-op`, async t => {
    const {store, contender} = await factory(t);
    await seed(store);
    const before = await head(store);
    const preview = await executeHandoffModeSet(store, {...request, dry_run: true});
    assert.equal(preview.status, "planned");
    assert.deepEqual(await head(store), before);
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
    const result = await executeHandoffModeSet(store, request);
    assert.equal(result.status, "applied", JSON.stringify(result));
    const after = await head(contender);
    assert.equal((after.head.todos as JsonObject[]).length, 464);
    assert.deepEqual(after.head, {...before.head, handoff_mode: "soft_claim"} as JsonObject);
    const noop = {...request, operation_id: "unchanged-mode"};
    const unchanged = await executeHandoffModeSet(store, noop);
    assert.equal(unchanged.changed, false);
    assert.equal((await store.readReceipt(noop.operation_id)).status, "found");
    assert.equal((await executeHandoffModeSet(store, {...request, operation_id: "later-mode", requested_mode: "hard_lease"})).status, "applied");
    const later = await head(store);
    for (const retry of [request, noop]) {
      const replay = await executeHandoffModeSet(contender, {...retry, observed_at: "2028-01-01T00:00:00Z"});
      assert.equal(replay.status, "replayed");
      assert.equal(replay.changed, false);
      assert.deepEqual(await head(store), later);
    }
    const mismatch = await executeHandoffModeSet(store, {...request, requested_mode: "hard_lease"});
    assert.equal(mismatch.reason_code, "coordination_operation_identity_mismatch");
    assert.equal(mismatch.failure_kind, "decision_rejection");
  });

  for (const kind of ["claim", "lease", "invalid_expiry", "unknown_lease_schema"] as const) {
    test(`${provider}: mode rejects ${kind} anywhere in the full authority without writing`, async t => {
      const {store} = await factory(t);
      const projection = quiescent();
      if (kind === "claim") {
        const todo = [...projection.todos as JsonObject[]].reverse().find(todo => todo.done !== true)!;
        todo.claimed_by = "agent-b";
        (projection.todo_read_model as JsonObject).records_sha256 = canonicalAuthoritySha256(projection.todos);
      } else {
        const lease = (projection.leases as JsonObject[]).at(-1)!;
        lease.status = "active";
        lease.expires_at = kind === "invalid_expiry" ? "invalid" : "2027-01-01T00:00:00Z";
        if (kind === "unknown_lease_schema") lease.schema_version = "unknown";
      }
      await seed(store, projection);
      const before = await head(store);
      const result = await executeHandoffModeSet(store, request);
      assert.equal(result.status, "failed", JSON.stringify(result));
      assert.equal(result.reason_code, kind === "claim" || kind === "lease"
        ? "handoff_mode_not_quiescent" : "invalid_handoff_mode_authority");
      assert.deepEqual(await head(store), before);
      assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
    });
  }

  test(`${provider}: expiry at observation time is quiescent and invalid modes never repair canonical state`, async t => {
    const {store} = await factory(t);
    const projection = quiescent();
    const lease = (projection.leases as JsonObject[])[0]!;
    lease.status = "active"; lease.expires_at = request.observed_at;
    await seed(store, projection);
    const before = await head(store);
    for (const invalid of [{requested_mode: "banana"}, {observed_at: "not-a-time"}]) {
      assert.equal((await executeHandoffModeSet(store, {...request, ...invalid})).status, "failed");
      assert.deepEqual(await head(store), before);
    }
    assert.equal((await executeHandoffModeSet(store, request)).status, "applied");
  });

  test(`${provider}: concurrent claim invalidates quiescence; no commit follows a stale snapshot`, async t => {
    const {store, contender} = await factory(t);
    await seed(store);
    const raced = intercept(store, async commit => {
      const current = await head(contender);
      const next = structuredClone(current.head);
      const todo = (next.todos as JsonObject[]).find(todo => todo.done !== true)!;
      todo.claimed_by = "agent-b";
      (next.todo_read_model as JsonObject).records_sha256 = canonicalAuthoritySha256(next.todos);
      assert.equal((await contender.commitAuthority({operation_id: "concurrent-claim", expected_provider_revision: current.provider_revision,
        next_projection: next, events: [], receipts: []})).status, "applied");
      return store.commitAuthority(commit);
    });
    assert.equal((await executeHandoffModeSet(raced, request)).status, "conflict");
    assert.equal((await head(store)).head.handoff_mode, "hard_lease");
    assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
    assert.equal((await executeHandoffModeSet(store, request)).reason_code, "handoff_mode_not_quiescent");
  });

  test(`${provider}: lost commit response recovers its receipt without replaying the change`, async t => {
    const {store} = await factory(t);
    await seed(store);
    const lost = intercept(store, async commit => {
      assert.equal((await store.commitAuthority(commit)).status, "applied");
      return {status: "ambiguous", reason_code: "response_lost", reason: "synthetic lost response"};
    });
    assert.equal((await executeHandoffModeSet(lost, request)).status, "recovered");
    const after = await head(store);
    assert.equal((await executeHandoffModeSet(store, request)).status, "replayed");
    assert.deepEqual(await head(store), after);
  });
  test(`${provider}: thrown post-commit response is recovered and never retried as a fresh write`, async t => {
    const {store} = await factory(t);
    await seed(store);
    let commits = 0;
    const lost = intercept(store, async commit => {
      commits++;
      assert.equal((await store.commitAuthority(commit)).status, "applied");
      throw new Error("transport disconnected after commit");
    });
    assert.equal((await executeHandoffModeSet(lost, request)).status, "recovered");
    assert.equal((await executeHandoffModeSet(lost, request)).status, "replayed");
    assert.equal(commits, 1);
  });

  test(`${provider}: unobserved receipt after commit requires same-operation recovery`, async t => {
    const {store} = await factory(t);
    await seed(store);
    let committed = false;
    const unavailable = intercept(store, async commit => {
      const result = await store.commitAuthority(commit);
      committed = true;
      return result;
    });
    unavailable.readReceipt = id => {
      if (committed) throw new Error("receipt connection unavailable");
      return store.readReceipt(id);
    };
    const result = await executeHandoffModeSet(unavailable, request);
    assert.equal(result.status, "ambiguous");
    assert.equal(result.reason_code, "coordination_receipt_recovery_required");
    assert.deepEqual(result.recovery, {operation_id: request.operation_id, retry_with_same_operation_id: true});
    const after = await head(store);
    assert.equal((await executeHandoffModeSet(store, request)).status, "replayed");
    assert.deepEqual(await head(store), after);
  });

  for (const corruption of ["changed", "previous_mode", "operation_id", "handoff_mode"] as const) {
    test(`${provider}: malformed historical ${corruption} decision cannot masquerade as a no-op`, async t => {
      const {store} = await factory(t);
      await seed(store);
      await executeHandoffModeSet(store, request);
      const before = await head(store);
      const corrupt = intercept(store, commit => store.commitAuthority(commit));
      corrupt.readReceipt = async id => {
        const receipt = await store.readReceipt(id);
        if (receipt.status !== "found") return receipt;
        const copied = structuredClone(receipt);
        const decision = copied.receipts[0]!.decision as JsonObject;
        if (corruption === "changed") delete decision.changed;
        else decision[corruption] = "wrong";
        return copied;
      };
      const result = await executeHandoffModeSet(corrupt, request);
      assert.equal(result.reason_code, "invalid_coordination_command_receipt");
      assert.equal(result.changed, false);
      assert.deepEqual(await head(store), before);
    });
  }

}
