/** Same independent lifecycle oracle across every AuthorityStore implementation. */
import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {executeCanonicalTaskLeaseLifecycle as execute,
  type CanonicalTaskLeaseLifecycleInput} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {productionScaleLeaseLifecycleFixture} from "./production_scale_coordination_fixture.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

async function loaded(store: AuthorityStore) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("fixture authority missing");
  return head;
}

export function registerLeaseLifecycleConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  async function setup(t: test.TestContext, schema: "native" | "legacy" = "native",
    change?: (projection: JsonObject, target: string) => void) {
    const {store, contender} = await factory(t);
    const fixture = productionScaleLeaseLifecycleFixture("goal-a", schema);
    const projection = fixture.projection as JsonObject;
    change?.(projection, fixture.target);
    const seed = authorityProjectionFixture("goal-a", projection.todos as JsonObject[], projection.leases as JsonObject[], schema,
      {handoff_mode: projection.handoff_mode});
    assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "lease-seed",
      next_projection: seed, events: [], receipts: []})).status, "applied");
    const scenario = fixture.scenario;
    const request: CanonicalTaskLeaseLifecycleInput = {operation: "transfer", goal_id: "goal-a", todo_id: fixture.target,
      owner: scenario.owner, idempotency_key: scenario.execution_key, expected_version: scenario.version,
      new_owner: scenario.receiver, new_idempotency_key: scenario.receiver_execution_key, ttl_seconds: 600,
      registered_agents: fixture.registered_agents, now: new Date(scenario.now)};
    return {store, contender, request, seed};
  }

  for (const schema of ["native", "legacy"] as const) {
    test(`${provider} ${schema} scale lease handover, renewal, cleanup and historical receipts`, async t => {
      const {store, contender, request, seed} = await setup(t, schema);
      const original = (seed.leases as JsonObject[]).find(l => l.todo_id === request.todo_id)!;
      const first = await execute(store, request);
      assert.equal(first.status, "applied", JSON.stringify(first));
      assert.deepEqual(first.lease, {...original, owner: "agent-b", idempotency_key: "lifecycle-b",
        version: 4, lease_epoch: 8, updated_at: "2026-09-13T10:05:00Z", expires_at: "2026-09-13T10:15:00Z"});
      const afterTransfer = await loaded(contender);
      const stale = await execute(store, {...request, operation: "release", new_owner: null, new_idempotency_key: null, ttl_seconds: null});
      assert.equal(stale.reason_code, "version_mismatch");
      assert.equal(stale.actual_version, 4); assert.equal(stale.expected_version, 3);
      assert.deepEqual(await loaded(contender), afterTransfer);
      const renew: CanonicalTaskLeaseLifecycleInput = {...request, operation: "renew", owner: "agent-b",
        idempotency_key: "lifecycle-b", expected_version: 4, new_owner: null, new_idempotency_key: null};
      const second = await execute(contender, renew);
      assert.equal(second.status, "applied");
      assert.deepEqual(second.lease, {...first.lease as JsonObject, version: 5});
      const release: CanonicalTaskLeaseLifecycleInput = {...renew, operation: "release", expected_version: 5, ttl_seconds: null,
        registered_agents: [], now: new Date("2026-09-14T10:05:00Z")};
      const third = await execute(store, release);
      assert.equal(third.status, "applied");
      assert.deepEqual(third.lease, {...second.lease as JsonObject, status: "released",
        released_at: "2026-09-14T10:05:00Z", updated_at: "2026-09-14T10:05:00Z"});
      const final = await loaded(contender);
      assert.equal(final.cursor, "4");
      assert.deepEqual(final.head.todos, seed.todos);
      assert.deepEqual((final.head.leases as JsonObject[]).filter(l => l.todo_id !== request.todo_id),
        (seed.leases as JsonObject[]).filter(l => l.todo_id !== request.todo_id));
      for (const [command, result] of [[request, first], [renew, second], [release, third]] as const) {
        const replay = await execute(contender, {...command, required_handoff_mode: "hard_lease",
          now: new Date("2030-01-01Z"), registered_agents: []});
        assert.equal(replay.status, "replayed"); assert.equal(replay.changed, false);
        assert.deepEqual(replay.original_receipt, result.original_receipt);
        assert.deepEqual(replay.lease, result.lease);
        assert.deepEqual(await loaded(store), final);
      }
      const conflict = await execute(store, {...request, new_idempotency_key: "different-receiver-key"});
      assert.equal(conflict.reason_code, "coordination_operation_identity_mismatch");
      assert.deepEqual(await loaded(store), final);
    });
  }

  test(`${provider} lease admission rejects claim conflict, exclusions, expiry and unsafe generations`, async t => {
    const {store, request} = await setup(t);
    const initial = await loaded(store);
    for (const [command, code] of [
      [{...request, new_owner: "unknown"}, "owner_not_registered"],
      [{...request, new_idempotency_key: request.idempotency_key}, "idempotency_key_reuse"],
      [{...request, expected_version: 0}, "version_mismatch"],
      [{...request, now: new Date("2028-01-01Z")}, "lease_not_active"],
    ] as const) {
      assert.equal((await execute(store, command)).reason_code, code);
      assert.deepEqual(await loaded(store), initial);
    }
  });

  for (const [dimension, code] of [["claim", "owner_conflicts_with_claim"], ["exclusion", "owner_excluded_from_todo"],
    ["archive", "todo_not_found"], ["version", "lease_generation_exhausted"], ["epoch", "lease_generation_exhausted"]] as const) {
    test(`${provider} scale lease rejects ${dimension} without a receipt or mutation`, async t => {
      const {store, request} = await setup(t, "native", (projection, target) => {
        const todo = (projection.todos as JsonObject[]).find(r => r.todo_id === target)!;
        const lease = (projection.leases as JsonObject[]).find(r => r.todo_id === target)!;
        if (dimension === "claim") todo.claimed_by = "agent-a";
        if (dimension === "exclusion") todo.excluded_agents = ["agent-b"];
        if (dimension === "archive") todo.archive_state = "archive";
        if (dimension === "version") lease.version = Number.MAX_SAFE_INTEGER;
        if (dimension === "epoch") lease.lease_epoch = Number.MAX_SAFE_INTEGER;
      });
      if (dimension === "version") request.expected_version = Number.MAX_SAFE_INTEGER;
      const before = await loaded(store);
      const result = await execute(store, request);
      assert.equal(result.reason_code, code, JSON.stringify(result));
      assert.deepEqual(await loaded(store), before);
    });
  }

  test(`${provider} missing and already released cleanup seal stable no-change receipts`, async t => {
    const {store, request} = await setup(t);
    const release: CanonicalTaskLeaseLifecycleInput = {...request, operation: "release", new_owner: null, new_idempotency_key: null,
      ttl_seconds: null, registered_agents: [], todo_id: "todo_missing", expected_version: 0};
    const first = await execute(store, release);
    assert.equal(first.status, "no_change", JSON.stringify(first)); assert.equal(first.changed, false);
    assert.equal(first.missing, true); assert.equal(first.released, false);
    const after = await loaded(store);
    assert.equal(after.cursor, "2");
    const replay = await execute(store, {...release, required_handoff_mode: "hard_lease"});
    assert.equal(replay.status, "replayed"); assert.deepEqual(replay.original_receipt, first.original_receipt);
    assert.deepEqual(await loaded(store), after);
    const retired = (after.head.leases as JsonObject[]).find(l => l.status === "released")!;
    const noChange = await execute(store, {...release, todo_id: String(retired.todo_id), owner: String(retired.owner),
      idempotency_key: String(retired.idempotency_key), expected_version: Number(retired.version)});
    assert.equal(noChange.status, "no_change"); assert.equal(noChange.released, true);
    assert.deepEqual((await loaded(store)).head, after.head);
  });

  for (const mode of ["soft_claim", "legacy"] as const) {
    test(`${provider} ${mode} cleanup remains local and cannot replay as a hard-lease command`, async t => {
      const {store, request} = await setup(t, "native", projection => { projection.handoff_mode = mode; });
      const release: CanonicalTaskLeaseLifecycleInput = {...request, operation: "release", new_owner: null,
        new_idempotency_key: null, ttl_seconds: null, todo_id: "todo_missing", expected_version: 0};
      const before = await loaded(store);
      const constrained = {...release, required_handoff_mode: "hard_lease" as const};
      assert.equal((await execute(store, constrained)).reason_code, "lease_handoff_mode_mismatch");
      assert.deepEqual(await loaded(store), before);
      const local = await execute(store, release);
      assert.equal(local.status, "no_change"); assert.equal(local.missing, true);
      assert.equal(local.handoff_mode, mode);
      const after = await loaded(store);
      assert.equal((await execute(store, constrained)).reason_code, "invalid_coordination_command_receipt");
      const replay = await execute(store, release);
      assert.equal(replay.status, "replayed"); assert.deepEqual(replay.original_receipt, local.original_receipt);
      assert.deepEqual(await loaded(store), after);
    });
  }

  test(`${provider} lost lease responses recover once and competing intents cannot overwrite`, async t => {
    const {store, contender, request} = await setup(t);
    const uncertain: AuthorityStore = {storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
      readReceipt: id => store.readReceipt(id), scanCommitted: (...args) => store.scanCommitted(...args),
      commitAuthority: async commit => {assert.equal((await store.commitAuthority(commit)).status, "applied"); throw new Error("lost response");}};
    const recovered = await execute(uncertain, request);
    assert.equal(recovered.status, "recovered");
    const after = await loaded(contender); assert.equal(after.cursor, "2");
    assert.equal((await execute(contender, request)).status, "replayed");
    assert.deepEqual(await loaded(store), after);
    // A concurrent head change is an explicit conflict, never automatic write retry.
    const renew: CanonicalTaskLeaseLifecycleInput = {...request, operation: "renew", owner: "agent-b", idempotency_key: "lifecycle-b",
      expected_version: 4, new_owner: null, new_idempotency_key: null};
    const loser = await execute(store, renew, async () => {
      assert.equal((await execute(contender, {...renew, ttl_seconds: 900})).status, "applied");
    });
    assert.equal(loser.reason_code, "coordination_operation_identity_mismatch");
    assert.equal((await loaded(store)).cursor, "3");
  });
}
