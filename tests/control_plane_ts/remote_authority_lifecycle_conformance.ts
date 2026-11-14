import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {prepareCoordinationProjectionCommit, type CoordinationProjectionMutation} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import type {RemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {postgresRemoteFixture, claimRequest, serviceIdentity} from "./remote_authority_fixture.ts";

function lifecycle(todo: string, operation: "renew" | "release", version = 0): RemoteAuthorityRequest {
  const {request_id: _, ...scope} = claimRequest("a", todo) as Extract<RemoteAuthorityRequest, {operation: "claim"}>;
  return {...scope, operation, idempotency_key: `existing-${todo}`, expected_version: version,
    ...(operation === "renew" ? {ttl_seconds: 600} : {})} as RemoteAuthorityRequest;
}
function localInput(todo: string, operation: "renew" | "release") {
  return {goal_id: "goal-one", todo_id: todo, operation, owner: "actor-a", registered_agents: ["actor-a", "actor-b"],
    idempotency_key: `existing-${todo}`, expected_version: 0, ttl_seconds: operation === "renew" ? 600 : null, now: new Date()};
}

export function registerRemoteAuthorityLifecycleConformance(connectionString: string | undefined) {
  test("remote lifecycle preserves existing canonical version-zero leases", {skip: !connectionString}, async t => {
    const fixture = await postgresRemoteFixture(t, connectionString!);
    const running = await fixture.start();
    await fixture.seed(["todo_remote_renew", "todo_remote_release", "todo_local_renew", "todo_local_release"]);
    const head = await fixture.store.loadAuthority(); assert.equal(head.status, "loaded");
    if (head.status !== "loaded") return;
    const now = new Date(), mutations: CoordinationProjectionMutation[] = [];
    for (const todo of head.head.todos as JsonObject[]) {
      mutations.push({kind: "todo_upsert", todo: {...todo, claimed_by: "actor-a"}}, {kind: "lease_upsert", lease: {
        schema_version: "task_lease_v0", goal_id: "goal-one", todo_id: todo.todo_id, owner: "actor-a", status: "active",
        idempotency_key: `existing-${todo.todo_id}`, version: 0, lease_epoch: 1, write_scopes: [`${todo.todo_id}/**`],
        acquired_at: now.toISOString(), updated_at: now.toISOString(), expires_at: new Date(+now + 3600000).toISOString(),
      }});
    }
    assert.equal((await fixture.store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: "goal-one",
      operation_id: "existing-zero-leases", expected_provider_revision: head.provider_revision, projection: head.head, mutations}))).status, "applied");
    for (const operation of ["renew", "release"] as const) {
      await t.test(`read then ${operation} accepts the same version zero as the canonical owner`, async () => {
        const control = await executeCanonicalTaskLeaseLifecycle(fixture.store, localInput(`todo_local_${operation}`, operation));
        assert.equal(control.status, "applied");
        assert.equal((control.lease as JsonObject).version, operation === "renew" ? 1 : 0);
        const todo = `todo_remote_${operation}`, original = lifecycle(todo, operation);
        const {request_id: _, ...scope} = claimRequest("a", todo) as Extract<RemoteAuthorityRequest, {operation: "claim"}>;
        const read = await fixture.client(running.endpoint, "a", {...scope, operation: "read"});
        assert.equal(read.code, 0); assert.equal((read.value.lease as JsonObject).version, 0);
        assert.equal((read.value.lease as JsonObject).status, "active");
        const completed = await fixture.client(running.endpoint, "a", original);
        assert.equal(completed.code, 0);
        assert.equal((completed.value.result as JsonObject).status, "applied");
        const lease = (completed.value.result as JsonObject).lease as JsonObject;
        assert.equal(lease.version, operation === "renew" ? 1 : 0);
        assert.equal(lease.status, operation === "renew" ? "active" : "released");
        if (operation === "renew") {
          const before = await fixture.store.loadAuthority();
          const stale = await fixture.client(running.endpoint, "a", lifecycle(todo, "release", 0));
          assert.equal(stale.code, 1); assert.equal((stale.value.result as JsonObject).reason_code, "version_mismatch");
          const staleRenew = {...original, ttl_seconds: 300};
          assert.equal((await fixture.client(running.endpoint, "a", staleRenew)).code, 1);
          assert.deepEqual(await fixture.store.loadAuthority(), before);
          assert.equal((await fixture.client(running.endpoint, "a", lifecycle(todo, "release", 1))).code, 0);
        }
        const beforeRecovery = await fixture.store.loadAuthority();
        const recovered = await fixture.client(running.endpoint, "a", original, true);
        assert.equal(recovered.code, 0); assert.equal(recovered.value.historical_only, true);
        assert.equal(((recovered.value.result as JsonObject).lease as JsonObject).version, lease.version);
        assert.deepEqual(await fixture.store.loadAuthority(), beforeRecovery);
      });
    }
  });

  test("remote version-zero release preserves the canonical missing-lease no-op", {skip: !connectionString}, async t => {
    const fixture = await postgresRemoteFixture(t, connectionString!);
    const running = await fixture.start();
    await fixture.seed(["todo_remote_missing", "todo_local_missing"]);
    const control = await executeCanonicalTaskLeaseLifecycle(fixture.store, localInput("todo_local_missing", "release"));
    assert.equal(control.status, "no_change"); assert.equal(control.missing, true); assert.equal(control.released, false);
    const request = lifecycle("todo_remote_missing", "release"), before = await fixture.store.loadAuthority();
    const completed = await fixture.client(running.endpoint, "a", request);
    assert.equal(completed.code, 0);
    const result = completed.value.result as JsonObject;
    assert.equal(result.status, "no_change"); assert.equal(result.changed, false);
    assert.equal(result.missing, true); assert.equal(result.released, false); assert.equal(result.lease, undefined);
    const committed = await fixture.store.loadAuthority();
    assert.equal(committed.status, "loaded");
    if (committed.status === "loaded" && before.status === "loaded") assert.deepEqual(committed.head, before.head);
    const replay = await fixture.client(running.endpoint, "a", request);
    assert.equal(replay.code, 0); assert.equal((replay.value.result as JsonObject).status, "replayed");
    const recovered = await fixture.client(running.endpoint, "a", request, true);
    assert.equal(recovered.code, 0); assert.equal(recovered.value.historical_only, true);
    assert.equal((recovered.value.result as JsonObject).missing, true);
    assert.deepEqual(await fixture.store.loadAuthority(), committed, "replay and recovery retain the original no-op receipt");
  });

  test("remote authority full lifecycle: snapshot, todo_add, complete, block, and defer", {skip: !connectionString}, async t => {
    const fixture = await postgresRemoteFixture(t, connectionString!);
    const running = await fixture.start();
    await fixture.seed(["todo_for_complete", "todo_for_block", "todo_for_defer"]);

    const baseScope = {
      schema_version: "loopx_remote_authority_request_v0" as const,
      tenant_id: "tenant-one",
      goal_id: "goal-one",
      actor_id: "actor-a",
      store_identity: serviceIdentity,
    };

    // 1. Snapshot
    const snap = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      operation: "snapshot",
    });
    assert.equal(snap.code, 0);
    assert.equal(snap.value.status, "snapshot");
    assert(Array.isArray(snap.value.todos));
    assert(Array.isArray(snap.value.leases));
    assert.equal(typeof snap.value.receipt_sha256, "string");

    // 2. Todo add
    const added = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_created_remotely",
      operation: "todo_add",
      request_id: "req-add-remotely",
      text: "[P1] remotely created task",
      priority: "P1",
      role: "agent",
    });
    assert.equal(added.code, 0);
    assert.equal((added.value.result as JsonObject).status, "applied");
    assert.equal((added.value.result as JsonObject).todo_id, "todo_created_remotely");

    // 3. Claim -> Complete
    const claimComp = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_for_complete",
      operation: "claim",
      request_id: "claim-for-comp",
    });
    assert.equal(claimComp.code, 0);
    assert.equal((claimComp.value.result as JsonObject).status, "applied");

    const comp = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_for_complete",
      operation: "complete",
      request_id: "complete-req-1",
      claim_request_id: "claim-for-comp",
      evidence: "sha256:1111 delivery validated",
    });
    assert.equal(comp.code, 0);
    assert.equal((comp.value.result as JsonObject).status, "applied");
    assert.equal((comp.value.result as JsonObject).completed, true);

    // 4. Claim -> Block
    const claimBlock = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_for_block",
      operation: "claim",
      request_id: "claim-for-block",
    });
    assert.equal(claimBlock.code, 0);

    const block = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_for_block",
      operation: "block",
      request_id: "block-req-1",
      claim_request_id: "claim-for-block",
      reason: "external dependency blocked",
    });
    assert.equal(block.code, 0);
    assert.equal((block.value.result as JsonObject).status, "applied");

    // 5. Claim -> Defer
    const claimDefer = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_for_defer",
      operation: "claim",
      request_id: "claim-for-defer",
    });
    assert.equal(claimDefer.code, 0);

    const defer = await fixture.client(running.endpoint, "a", {
      ...baseScope,
      todo_id: "todo_for_defer",
      operation: "defer",
      request_id: "defer-req-1",
      claim_request_id: "claim-for-defer",
      reason: "deferred until milestone",
      resume_when: "resume_at:2026-12-01T00:00:00Z",
    });
    assert.equal(defer.code, 0);
    assert.equal((defer.value.result as JsonObject).status, "applied");
  });
}
