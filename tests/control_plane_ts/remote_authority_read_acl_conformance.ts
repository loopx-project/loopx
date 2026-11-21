import assert from "node:assert/strict";
import test from "node:test";
import {createServer} from "node:https";
import type {AddressInfo} from "node:net";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {transitionGoalAcceptanceLifecycle} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import type {GoalAcceptanceLifecycleTransition} from "../../loopx/control_plane/goals/acceptance_lifecycle.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationTodoCreate} from "../../loopx/control_plane/coordination/todo_create.ts";
import {REMOTE_AUTHORITY_REQUEST, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE,
  remoteAuthorityOperationId, remoteAuthoritySnapshotDigest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {REMOTE_AUTHORITY_SCOPED_READ_POLICY} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {claimRequest, postgresRemoteFixture, serviceIdentity, servicePolicy} from "./remote_authority_fixture.ts";

export function registerRemoteAuthorityReadAclConformance(connectionString: string | undefined) {
  test("remote read ACL pins the Goal lifetime and projects only the caller's resources", {skip: !connectionString}, async t => {
    const fixture = await postgresRemoteFixture(t, connectionString!);
    const goal_id = "goal-one";
    const a = {goal_id, goal_instance_id: "ginst_" + "a".repeat(32)};
    const b = {goal_id, goal_instance_id: "ginst_" + "b".repeat(32)};
    const {scopes, principals} = servicePolicy();
    const policy = {schema_version: REMOTE_AUTHORITY_SCOPED_READ_POLICY,
      scopes: scopes.map(scope => ({...scope, todo_read_acl: [
        {goal_ref: a, todo_id: "todo_visible", principal_id: "principal-a", actor_id: "actor-a",
          todo_origin_operation_id: "bind-first-goal"},
        {goal_ref: b, todo_id: "todo_next", principal_id: "principal-b", actor_id: "actor-b",
          todo_origin_operation_id: "create-successor-todo"},
        // A new grant must not relabel retained predecessor resources.
        {goal_ref: b, todo_id: "todo_visible", principal_id: "principal-b", actor_id: "actor-b",
          todo_origin_operation_id: "bind-first-goal"},
      ]})),
      principals: principals.map((principal, index) => ({...principal, grants: principal.grants.map(grant => ({
        ...grant, goal_ref: index === 0 ? a : b, operations: ["read", "snapshot"],
      }))})),
    };
    await fixture.seed(["todo_visible", "todo_hidden"], {todo_visible: "unqualified source text", todo_hidden: "private hidden note"});
    const legacy = await fixture.start();
    const originalClaim = claimRequest("a", "todo_visible");
    assert.equal((await fixture.client(legacy.endpoint, "a", originalClaim)).code, 0);
    assert.equal((await fixture.client(legacy.endpoint, "b", claimRequest("b", "todo_hidden"))).code, 0);
    await legacy.stop();
    await fixture.writePolicy(policy);
    const service = await fixture.start();
    const scope = (actor: "a" | "b") => ({schema_version: REMOTE_AUTHORITY_REQUEST, goal_id,
      tenant_id: "tenant-one", actor_id: `actor-${actor}`, store_identity: serviceIdentity} as const);
    const snapshot = (actor: "a" | "b", todo_id?: string) =>
      fixture.client(service.endpoint, actor, {...scope(actor), operation: "snapshot", ...(todo_id ? {todo_id} : {})});
    const read = (actor: "a" | "b", todo_id: string) =>
      fixture.client(service.endpoint, actor, {...scope(actor), operation: "read", todo_id});
    function denied(response: Awaited<ReturnType<typeof read>>) {
      assert.equal(response.code, 1);
      assert.equal(response.value.status, "rejected");
      assert.equal(response.value.reason_code, "remote_authority_scope_denied");
      assert.deepEqual(Object.keys(response.value).sort(), ["reason_code", "request_sha256", "schema_version", "status"]);
    }
    async function transition(operation_id: string, step: GoalAcceptanceLifecycleTransition, expected = "applied") {
      const result = await transitionGoalAcceptanceLifecycle(fixture.store, {
        goal_id, actor_agent_id: null, operation_id, transition: step,
      });
      assert.equal(result.status, expected);
    }

    const unbound = await fixture.store.loadAuthority();
    denied(await read("a", "todo_visible"));
    denied(await snapshot("a"));
    assert.deepEqual(await fixture.store.loadAuthority(), unbound);
    await transition("bind-first-goal", {kind: "bind_existing", goal_ref: a});
    const head = await fixture.store.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status !== "loaded") throw new Error("Missing fixture head");

    const visible = await read("a", "todo_visible");
    assert.equal(visible.code, 0);
    assert.equal(visible.value.schema_version, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE);
    assert.deepEqual(visible.value.goal_ref, a);
    assert.deepEqual(visible.value.todo, {todo_id: "todo_visible", status: "open", claimed_by: "actor-a"});
    assert.equal((visible.value.lease as JsonObject).owner, "actor-a");
    assert(!("idempotency_key" in (visible.value.lease as JsonObject)));
    const first = await snapshot("a");
    assert.equal(first.code, 0);
    assert.deepEqual(first.value.goal_ref, a);
    for (const field of ["todos", "leases"]) {
      assert.deepEqual((first.value[field] as JsonObject[]).map(row => row.todo_id), ["todo_visible"]);
    }
    assert(!Object.hasOwn(first.value, "provider_revision"));
    assert(!JSON.stringify(first.value).includes("unqualified source text"));
    await t.test("TLS CLI rejects nested disclosure even with correlation and a recomputed snapshot digest", async () => {
      let reply: JsonObject = visible.value;
      const proxy = createServer({key: fixture.key, cert: fixture.cert}, (incoming, outgoing) => {
        incoming.resume(); outgoing.setHeader("Content-Type", "application/json"); outgoing.end(JSON.stringify(reply));
      });
      await new Promise<void>(resolve => proxy.listen(0, "127.0.0.1", resolve));
      try {
        const endpoint = `https://127.0.0.1:${(proxy.address() as AddressInfo).port}`;
        const request = {...scope("a"), operation: "read" as const, todo_id: "todo_visible"};
        assert.equal((await fixture.client(endpoint, "a", request)).code, 0);
        for (const [record, field] of [["todo", "note"], ["lease", "idempotency_key"]]) {
          reply = {...visible.value, [record!]: {...visible.value[record!] as JsonObject, [field!]: "private-response-marker"}};
          const rejected = await fixture.client(endpoint, "a", request);
          assert.equal(rejected.code, 1); assert.equal(rejected.value.status, "unavailable");
          assert(!JSON.stringify(rejected.value).includes("private-response-marker"));
        }
        const snapshotRequest = {...scope("a"), operation: "snapshot" as const};
        for (const [collection, field] of [["todos", "note"], ["leases", "idempotency_key"]]) {
          reply = structuredClone(first.value);
          (reply[collection!] as JsonObject[])[0]![field!] = "private-response-marker";
          reply.receipt_sha256 = remoteAuthoritySnapshotDigest(snapshotRequest, undefined,
            reply.todos as JsonObject[], reply.leases as JsonObject[], a);
          const rejected = await fixture.client(endpoint, "a", snapshotRequest);
          assert.equal(rejected.code, 1); assert.equal(rejected.value.status, "unavailable");
          assert(!JSON.stringify(rejected.value).includes("private-response-marker"));
        }
        reply = first.value;
        assert.equal((await fixture.client(endpoint, "a", snapshotRequest)).code, 0);
      } finally { await new Promise<void>(resolve => proxy.close(() => resolve())); }
    });
    for (const origin of [undefined, "missing-origin-operation", remoteAuthorityOperationId(originalClaim, "principal-a")]) {
      await fixture.writePolicy({...policy, scopes: policy.scopes.map(s => ({...s,
        todo_read_acl: s.todo_read_acl.map(entry => ({...entry, todo_origin_operation_id: origin}))}))});
      denied(await read("a", "todo_visible"));
      denied(await snapshot("a"));
    }
    await fixture.writePolicy(policy);
    denied(await read("a", "todo_hidden"));
    denied(await read("a", "todo_missing"));
    denied(await snapshot("a", "todo_hidden"));
    denied(await snapshot("b"));
    denied(await fixture.client(service.endpoint, "a", {...scope("b"), operation: "snapshot"}));
    denied(await fixture.client(service.endpoint, "a", originalClaim));
    denied(await fixture.client(service.endpoint, "a", originalClaim, true));
    assert.deepEqual(await fixture.store.loadAuthority(), head);

    const hidden = (head.head.todos as JsonObject[]).find(todo => todo.todo_id === "todo_hidden")!;
    assert.equal((await fixture.store.commitAuthority(prepareCoordinationProjectionCommit({
      goal_id, projection: head.head, expected_provider_revision: head.provider_revision, operation_id: "change-hidden-todo",
      mutations: [{kind: "todo_upsert", todo: {...hidden, note: "different private hidden note"}}],
    }))).status, "applied");
    assert.deepEqual((await snapshot("a")).value, first.value);

    await fixture.writePolicy({...policy, scopes: policy.scopes.map(s => ({...s,
      todo_read_acl: s.todo_read_acl.filter(entry => entry.principal_id !== "principal-a")}))});
    denied(await snapshot("a"));
    await fixture.writePolicy(servicePolicy());
    const downgrade = await snapshot("a");
    assert.equal(downgrade.value.status, "unavailable");
    assert.equal(downgrade.value.reason_code, "remote_authority_policy_unavailable");
    await fixture.writePolicy(policy);
    assert.deepEqual((await snapshot("a")).value, first.value);

    await transition("retire-first-goal", {kind: "retire", goal_ref: a});
    denied(await snapshot("a"));
    await transition("activate-second-goal", {kind: "activate_successor", retired_goal_ref: a, goal_ref: b});
    const added = await executeCoordinationTodoCreate(fixture.store, {
      goal_id, actor_agent_id: "actor-b", registered_agents: ["actor-a", "actor-b"],
      operation_id: "create-successor-todo", dry_run: false, now: new Date(),
      todo: {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_next", role: "agent",
        status: "open", done: false, archive_state: "active", text: "Second lifetime task"},
    });
    assert.equal(added.status, "applied");
    const successor = await fixture.store.loadAuthority();
    denied(await snapshot("a"));
    denied(await read("a", "todo_visible"));
    denied(await read("a", "todo_next"));
    denied(await read("b", "todo_visible"));
    const second = await snapshot("b");
    assert.equal(second.code, 0);
    assert.deepEqual(second.value.goal_ref, b);
    assert.deepEqual((second.value.todos as JsonObject[]).map(todo => todo.todo_id), ["todo_next"]);
    assert.deepEqual(second.value.leases, []);
    assert.equal((await read("b", "todo_next")).code, 0);
    assert.deepEqual(await fixture.store.loadAuthority(), successor);
    await transition("bind-current-again", {kind: "bind_existing", goal_ref: b}, "no_change");
    for (const origin of ["activate-second-goal", "bind-current-again", "create-successor-todo"]) {
      await fixture.writePolicy({...policy, scopes: policy.scopes.map(s => ({...s,
        todo_read_acl: s.todo_read_acl.map(entry => entry.goal_ref === b && entry.todo_id === "todo_visible"
          ? {...entry, todo_origin_operation_id: origin} : entry)}))});
      denied(await read("b", "todo_visible"));
      assert.deepEqual(((await snapshot("b")).value.todos as JsonObject[]).map(todo => todo.todo_id), ["todo_next"]);
    }
  });

  test("scoped receipt recovery proves the original Goal lifetime and current exact operation access", {skip: !connectionString}, async t => {
    const fixture = await postgresRemoteFixture(t, connectionString!);
    const goal_id = "goal-one";
    const a = {goal_id, goal_instance_id: "ginst_" + "a".repeat(32)};
    const b = {goal_id, goal_instance_id: "ginst_" + "b".repeat(32)};
    const unboundClaim = claimRequest("a", "todo_legacy");
    const boundClaim = claimRequest("a", "todo_bound");
    const missingClaim = claimRequest("a", "todo_bound", "never-committed");
    const id = (request: typeof boundClaim) => remoteAuthorityOperationId(request, "principal-a");
    async function transition(operation_id: string, step: GoalAcceptanceLifecycleTransition) {
      const result = await transitionGoalAcceptanceLifecycle(fixture.store, {goal_id, actor_agent_id: null,
        operation_id, transition: step});
      assert.equal(result.status, "applied");
    }

    await fixture.seed(["todo_legacy", "todo_bound", "todo_hidden"]);
    const legacy = await fixture.start();
    assert.equal((await fixture.client(legacy.endpoint, "a", unboundClaim)).code, 0);
    await transition("bind-receipt-goal", {kind: "bind_existing", goal_ref: a});
    assert.equal((await fixture.client(legacy.endpoint, "a", boundClaim)).code, 0);
    const original = await fixture.client(legacy.endpoint, "a", boundClaim, true);
    assert.equal(original.code, 0);
    assert("cursor" in original.value);
    assert("lease" in (original.value.result as JsonObject));
    await legacy.stop();

    // Read an earlier receipt after the authority has crossed a checkpoint.
    // This uses the normal journal and keeps the original receipt intact.
    for (let sequence = 0; sequence < 64; sequence++) {
      const head = await fixture.store.loadAuthority();
      if (head.status !== "loaded") throw new Error("Missing fixture head");
      const hidden = (head.head.todos as JsonObject[]).find(todo => todo.todo_id === "todo_hidden")!;
      const result = await fixture.store.commitAuthority(prepareCoordinationProjectionCommit({
        goal_id, projection: head.head, expected_provider_revision: head.provider_revision,
        operation_id: `append-receipt-fixture-${sequence}`,
        mutations: [{kind: "todo_upsert", todo: {...hidden, note: `Hidden fixture update ${sequence}`}}],
      }));
      assert.equal(result.status, "applied");
    }
    function policy(goalRef: typeof a, allowed: readonly string[], operations = ["read", "snapshot"]) {
      const {scopes, principals} = servicePolicy();
      return {schema_version: REMOTE_AUTHORITY_SCOPED_READ_POLICY,
        scopes: scopes.map(scope => ({...scope, todo_read_acl: [
          {goal_ref: goalRef, todo_id: "todo_bound", principal_id: "principal-a", actor_id: "actor-a",
            todo_origin_operation_id: "bind-receipt-goal", receipt_operation_ids: allowed},
          {goal_ref: goalRef, todo_id: "todo_legacy", principal_id: "principal-a", actor_id: "actor-a",
            receipt_operation_ids: [id(unboundClaim), id(boundClaim)]},
        ]})),
        principals: principals.slice(0, 1).map(principal => ({...principal, grants: principal.grants.map(grant =>
          ({...grant, goal_ref: goalRef, operations}))})),
      };
    }
    await fixture.writePolicy(policy(a, [id(boundClaim), id(missingClaim)]));
    const service = await fixture.start();
    const recover = (request: typeof boundClaim) => fixture.client(service.endpoint, "a", request, true);
    function denied(result: Awaited<ReturnType<typeof recover>>) {
      assert.equal(result.code, 1);
      assert.equal(result.value.status, "rejected");
      assert.equal(result.value.reason_code, "remote_authority_scope_denied");
      assert.deepEqual(Object.keys(result.value).sort(), ["reason_code", "request_sha256", "schema_version", "status"]);
    }
    const current = await fixture.store.loadAuthority();
    const allowed = await recover(boundClaim);
    assert.equal(allowed.code, 0);
    assert.deepEqual(allowed.value, {
      schema_version: REMOTE_AUTHORITY_SCOPED_READ_RESPONSE, status: "found", historical_only: true,
      todo_id: boundClaim.todo_id, goal_ref: a, operation_id: original.value.operation_id,
      receipt_sha256: original.value.receipt_sha256, request_sha256: original.value.request_sha256,
    });
    denied(await recover(unboundClaim));
    denied(await recover(missingClaim));
    denied(await recover({...boundClaim, todo_id: unboundClaim.todo_id}));
    denied(await fixture.client(service.endpoint, "a", boundClaim)); // Execute stays outside the read grant.
    await fixture.writePolicy(policy(a, []));
    denied(await recover(boundClaim));
    const currentTodo = await fixture.client(service.endpoint, "a", {
      operation: "read", schema_version: REMOTE_AUTHORITY_REQUEST, tenant_id: boundClaim.tenant_id,
      goal_id, actor_id: boundClaim.actor_id, store_identity: serviceIdentity, todo_id: boundClaim.todo_id,
    });
    assert.equal(currentTodo.code, 0);
    await fixture.writePolicy(policy(a, [id(boundClaim)], ["snapshot"]));
    denied(await recover(boundClaim));
    assert.deepEqual(await fixture.store.loadAuthority(), current);

    await fixture.writePolicy(policy(a, [id(boundClaim)]));
    await transition("retire-receipt-goal", {kind: "retire", goal_ref: a});
    denied(await recover(boundClaim));
    await transition("activate-next-receipt-goal", {kind: "activate_successor", retired_goal_ref: a, goal_ref: b});
    const successor = await fixture.store.loadAuthority();
    denied(await recover(boundClaim)); // Stale credential.
    await fixture.writePolicy(policy(b, [id(boundClaim)]));
    denied(await recover(boundClaim)); // Current ACL cannot reassign a receipt from A to B.
    assert.deepEqual(await fixture.store.loadAuthority(), successor);
  });
}
