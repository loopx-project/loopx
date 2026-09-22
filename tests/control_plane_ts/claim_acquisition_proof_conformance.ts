/** Atomic adoption must meet the same present-tense execution proof as acquire.
 * Receipt history is immutable even when current execution changes. */
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoClaim as claim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {executeCanonicalTaskLeaseLifecycle as maintain} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {productionScaleLeaseAcquisitionFixture} from "./production_scale_coordination_fixture.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

async function head(store: AuthorityStore) {
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") throw new Error("fixture authority unavailable");
  return loaded;
}

export function registerClaimAcquisitionProofConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  async function setup(t: test.TestContext, schema: "native" | "legacy") {
    const {store, contender} = await factory(t);
    const f = productionScaleLeaseAcquisitionFixture("goal-a", schema);
    const projection = authorityProjectionFixture("goal-a", (f.projection.todos as JsonObject[]).map(todo =>
      todo.todo_id === f.target ? {...todo, required_write_scopes: ["lease-admission/**"]} : todo),
      f.projection.leases as JsonObject[], schema, {handoff_mode: "hard_lease"});
    assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      next_projection: projection, events: [], receipts: []})).status, "applied");
    const request = {goal_id: "goal-a", todo_id: f.target, claimed_by: "agent-a", actor_agent_id: "agent-a",
      expected_role: "agent", registered_agents: f.registered_agents, operation_id: "claim-acquire",
      lease_request: {idempotency_key: "execution-a", expected_version: 0, ttl_seconds: 600},
      dry_run: false, now: new Date(f.scenario.now)};
    const maintenance = {goal_id: request.goal_id, todo_id: request.todo_id, owner: "agent-a",
      idempotency_key: "execution-a", expected_version: 1, ttl_seconds: 600,
      registered_agents: request.registered_agents, now: new Date("2026-09-13T10:06:00Z")};
    return {store, contender, request, maintenance, projection};
  }

  for (const schema of ["native", "legacy"] as const) {
    test(`${provider} claim proof: ${schema} renewal returns current proof and retains original receipt`, async t => {
      const {store, contender, request, maintenance} = await setup(t, schema);
      const first = await claim(store, request);
      assert.equal(first.status, "applied", JSON.stringify(first));
      const original = await store.readReceipt(request.operation_id);
      const renewed = await maintain(contender, {...maintenance, operation: "renew"});
      assert.equal(renewed.status, "applied", JSON.stringify(renewed));
      const before = await head(store);
      const replay = await claim(store, {...request, now: maintenance.now});
      assert.equal(replay.status, "replayed", JSON.stringify(replay));
      assert.equal(replay.changed, false);
      assert.equal(replay.provider_revision, first.provider_revision, "receipt revision remains historical");
      assert.equal(replay.current_provider_revision, before.provider_revision);
      assert.equal((replay.lease as JsonObject).version, 2);
      assert.equal((replay.lease as JsonObject).lease_epoch, 1);
      assert.deepEqual(replay.lease, renewed.lease);
      assert.deepEqual(replay.original_receipt, first.original_receipt);
      assert.deepEqual(await store.readReceipt(request.operation_id), original);
      assert.deepEqual(await head(store), before);
    });

    for (const retirement of ["release", "transfer", "expiry"] as const) {
      test(`${provider} claim proof: ${schema}/${retirement} cannot replay current execution`, async t => {
        const {store, contender, request, maintenance} = await setup(t, schema);
        assert.equal((await claim(store, request)).status, "applied");
        const original = await store.readReceipt(request.operation_id);
        if (retirement !== "expiry") {
          const result = await maintain(contender, {...maintenance, operation: retirement,
            ttl_seconds: retirement === "release" ? null : 600,
            ...(retirement === "transfer" ? {new_owner: "agent-b", new_idempotency_key: "receiver-b", transfer_claim: true} : {})});
          assert.equal(result.status, "applied", JSON.stringify(result));
        }
        const before = await head(store);
        const result = await claim(store, {...request,
          now: new Date(retirement === "expiry" ? "2026-09-13T10:15:00Z" : "2026-09-13T10:07:00Z")});
        assert.equal(result.status, "failed", JSON.stringify(result));
        assert.equal(result.reason_code, retirement === "transfer" ? "owner_conflicts_with_claim" : "idempotency_key_reuse");
        assert.equal(result.lease, undefined, "failure must not carry usable old proof");
        assert.deepEqual(await store.readReceipt(request.operation_id), original);
        assert.deepEqual(await head(store), before);
      });
    }

    for (const dimension of ["excluded", "unregistered", "archived", "done", "claim", "soft-mode", "legacy-mode"] as const) {
      test(`${provider} claim proof: ${schema}/${dimension} current eligibility supersedes history`, async t => {
        const {store, request} = await setup(t, schema);
        assert.equal((await claim(store, request)).status, "applied");
        const before = await head(store);
        const todo = (before.head.todos as JsonObject[]).find(row => row.todo_id === request.todo_id)!;
        const patch: JsonObject = dimension === "excluded" ? {excluded_agents: ["agent-a"]}
          : dimension === "archived" ? {archive_state: "archive"}
          : dimension === "done" ? {status: "done", done: true}
          : dimension === "claim" ? {claimed_by: "agent-b"} : {};
        if (Object.keys(patch).length) {
          assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
            operation_id: "eligibility-change", expected_provider_revision: before.provider_revision,
            projection: before.head, mutations: [{kind: "todo_upsert", todo: {...todo, ...patch}}]}))).status, "applied");
        } else if (dimension.endsWith("mode")) {
          assert.equal((await store.commitAuthority({operation_id: "mode-change", expected_provider_revision: before.provider_revision,
            next_projection: {...before.head, handoff_mode: dimension === "soft-mode" ? "soft_claim" : "legacy"}, events: [], receipts: []})).status, "applied");
        }
        const final = await head(store);
        const replay = await claim(store, {...request, registered_agents: dimension === "unregistered" ? ["agent-b"] : request.registered_agents});
        assert.equal(replay.status, "failed", JSON.stringify(replay));
        assert.equal(replay.lease, undefined);
        assert.deepEqual(await head(store), final);
      });
    }

    test(`${provider} claim proof: ${schema} missing current head is ambiguous and same-operation recoverable`, async t => {
      const {store, request} = await setup(t, schema);
      const first = await claim(store, request);
      assert.equal(first.status, "applied");
      const before = await head(store);
      for (const throws of [false, true]) {
        const unavailable = new Proxy(store, {get(target, key) {
          if (key === "loadAuthority") return async () => {
            if (throws) throw new Error("synthetic lost read response");
            return {status: "unavailable", reason_code: "offline", reason: "synthetic offline provider"};
          };
          const value = Reflect.get(target, key, target);
          return typeof value === "function" ? value.bind(target) : value;
        }});
        const result = await claim(unavailable, request);
        assert.equal(result.status, "ambiguous", JSON.stringify(result));
        assert.equal(result.reason_code, "canonical_acquire_readback_required");
        assert.equal(result.lease, undefined);
        assert.deepEqual(result.recovery, {operation_id: request.operation_id, retry_with_same_operation_id: true});
      }
      const recovered = await claim(store, request);
      assert.equal(recovered.status, "replayed");
      assert.deepEqual(recovered.lease, first.lease);
      assert.deepEqual(await head(store), before);
    });

    test(`${provider} claim proof: ${schema} lost acknowledgment still requires current execution`, async t => {
      const {store, contender, request, maintenance} = await setup(t, schema);
      let writes = 0;
      const intercepted = new Proxy(store, {get(target, key) {
        if (key === "commitAuthority") return async (commit: AuthorityStoreCommit) => {
          writes++;
          assert.equal((await target.commitAuthority(commit)).status, "applied");
          assert.equal((await maintain(contender, {...maintenance, operation: "release", ttl_seconds: null})).status, "applied");
          throw new Error("response lost after commit and lease retirement");
        };
        const value = Reflect.get(target, key, target);
        return typeof value === "function" ? value.bind(target) : value;
      }});
      const result = await claim(intercepted, request);
      assert.equal(writes, 1);
      assert.equal(result.status, "failed", JSON.stringify(result));
      assert.equal(result.reason_code, "idempotency_key_reuse");
      const current = await head(store);
      assert.equal(current.cursor, "3", "claim and independent release each commit once");
      assert.equal((await store.readReceipt(request.operation_id)).status, "found");
      assert.equal((current.head.leases as JsonObject[]).find(row => row.todo_id === request.todo_id)!.status, "released");
    });

    test(`${provider} claim proof: ${schema} a no-op receipt also needs current proof`, async t => {
      const {store, contender, request, maintenance} = await setup(t, schema);
      assert.equal((await claim(store, request)).status, "applied");
      const noOp = {...request, operation_id: "adopt-existing-execution",
        lease_request: {...request.lease_request, expected_version: 1}};
      const first = await claim(contender, noOp);
      assert.equal(first.status, "no_change", JSON.stringify(first));
      assert.equal(first.changed, false);
      assert.equal(first.projection_delivery, "not_required");
      const before = await head(store);
      const replay = await claim(store, noOp);
      assert.equal(replay.status, "replayed");
      assert.equal(replay.changed, false);
      assert.deepEqual(replay.original_receipt, first.original_receipt);
      assert.deepEqual(await head(store), before);
      assert.equal((await maintain(contender, {...maintenance, operation: "release", ttl_seconds: null})).status, "applied");
      const released = await head(store);
      assert.equal((await claim(store, noOp)).reason_code, "idempotency_key_reuse");
      assert.deepEqual(await head(store), released);
    });

    test(`${provider} claim proof: ${schema} source witness is checked after current proof`, async t => {
      const {store, request} = await setup(t, schema);
      const first = await claim(store, request);
      assert.equal(first.status, "applied");
      const before = await head(store);
      let checks = 0;
      const result = await claim(store, request, async () => ++checks === 1);
      assert.equal(checks, 2);
      assert.equal(result.status, "failed", JSON.stringify(result));
      assert.equal(result.reason_code, "authority_source_changed");
      assert.equal(result.lease, undefined);
      assert.deepEqual(await head(store), before);
    });


    test(`${provider} claim proof: ${schema} changed request cannot reuse a successful claim identity`, async t => {
      const {store, request} = await setup(t, schema);
      assert.equal((await claim(store, request)).status, "applied");
      const before = await head(store);
      const original = await store.readReceipt(request.operation_id);
      for (const change of [
        {lease_request: {...request.lease_request, ttl_seconds: 900}},
        {lease_request: {...request.lease_request, expected_version: 1}},
        {lease_request: {...request.lease_request, idempotency_key: "different-execution"}},
        {claimed_by: "agent-b", actor_agent_id: "agent-b"},
      ]) {
        const replay = await claim(store, {...request, ...change});
        assert.equal(replay.status, "failed");
        assert.equal(replay.reason_code, "coordination_operation_identity_mismatch");
        assert.equal(replay.lease, undefined);
        assert.deepEqual(await head(store), before);
        assert.deepEqual(await store.readReceipt(request.operation_id), original);
      }
    });

    test(`${provider} claim proof: ${schema} acceptance rebinding is current authority`, async t => {
      const {store, request} = await setup(t, schema);
      const first = await claim(store, request);
      assert.equal(first.status, "applied");
      const before = await head(store);
      const document = {objective: "Validate the accepted task", non_goals: [],
        criteria: [{id: "outcome", description: "Focused check passes", validation_argv: [process.execPath, "-e", "process.exit(0)"]}],
        bindings: [{todo_id: request.todo_id, criterion_ids: ["outcome"]}]};
      assert.equal((await configureGoalAcceptance(store, {goal_id: request.goal_id, actor_agent_id: null,
        operation_id: "bind-acceptance", expected_provider_revision: before.provider_revision, document})).status, "applied");
      assert.equal((await claim(store, request)).status, "replayed", "valid owner binding permits current execution");
      const bound = await head(store);
      const todo = (bound.head.todos as JsonObject[]).find(row => row.todo_id === request.todo_id)!;
      assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
        operation_id: "change-bound-work", expected_provider_revision: bound.provider_revision, projection: bound.head,
        mutations: [{kind: "todo_upsert", todo: {...todo, text: "Materially different work needs owner rebind"}}]}))).status, "applied");
      const changed = await head(store);
      const result = await claim(store, request);
      assert.equal(result.status, "failed");
      assert.equal((result.goal_acceptance_guard as JsonObject).allowed, false);
      assert.equal(result.lease, undefined);
      assert.deepEqual(result.original_receipt, first.original_receipt);
      assert.deepEqual(await head(store), changed);
    });

    test(`${provider} claim proof: ${schema} new execution can replace a released generation`, async t => {
      const {store, request, maintenance} = await setup(t, schema);
      const first = await claim(store, request);
      assert.equal(first.status, "applied");
      assert.equal((await maintain(store, {...maintenance, operation: "release", ttl_seconds: null})).status, "applied");
      const next = {...request, operation_id: "next-claim", now: maintenance.now,
        lease_request: {idempotency_key: "execution-next", expected_version: 1, ttl_seconds: 600}};
      const result = await claim(store, next);
      assert.equal(result.status, "applied", JSON.stringify(result));
      assert.equal(result.todo_changed, false, "the existing owner need not be claimed twice");
      assert.equal(result.lease_changed, true);
      assert.equal((result.lease as JsonObject).lease_epoch, 2);
      assert.equal((result.lease as JsonObject).version, 2);
      const before = await head(store);
      assert.equal((await claim(store, request)).reason_code, "idempotency_key_reuse");
      const replay = await claim(store, next);
      assert.equal(replay.status, "replayed");
      assert.deepEqual(replay.lease, result.lease);
      assert.deepEqual(await head(store), before);
    });


    test(`${provider} claim proof: ${schema} lost acknowledgment recovers one still-current acquisition`, async t => {
      const {store, request} = await setup(t, schema);
      let writes = 0;
      const interrupted = new Proxy(store, {get(target, key) {
        if (key === "commitAuthority") return async (commit: AuthorityStoreCommit) => {
          writes++;
          assert.equal((await target.commitAuthority(commit)).status, "applied");
          throw new Error("synthetic acknowledgment loss");
        };
        const value = Reflect.get(target, key, target);
        return typeof value === "function" ? value.bind(target) : value;
      }});
      const recovered = await claim(interrupted, request);
      assert.equal(recovered.status, "recovered", JSON.stringify(recovered));
      assert.equal(writes, 1);
      assert.equal((recovered.lease as JsonObject).version, 1);
      const committed = await head(store);
      assert.equal(committed.cursor, "2");
      const replay = await claim(store, request);
      assert.equal(replay.status, "replayed");
      assert.deepEqual(replay.original_receipt, recovered.original_receipt);
      assert.deepEqual(replay.lease, recovered.lease);
      assert.deepEqual(await head(store), committed);
    });

  }
}
