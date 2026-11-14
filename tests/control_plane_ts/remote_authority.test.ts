import assert from "node:assert/strict";
import test from "node:test";
import {request as httpsRequest} from "node:https";
import {connect as tlsConnect} from "node:tls";
import {connect, type Socket} from "node:net";
import {once} from "node:events";
import {mkdtemp, writeFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {decodeRemoteAuthorityRequest, decodeRemoteAuthorityResponse, remoteAuthorityOperationId,
  remoteAuthorityResponse} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {decodeRemoteAuthorityPolicy, remoteAuthorityPolicyAdmission, readRemoteAuthorityPolicy} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {RemoteAuthorityService} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {callRemoteAuthority, createRemoteAuthorityServer} from "../../loopx/control_plane/coordination/remote_authority_http.ts";
import {claimRequest, credentials, servicePolicy, tlsFixture} from "./remote_authority_fixture.ts";

test("remote commands reject caller authority, time, raw state and unknown operations", () => {
  const request = claimRequest("a");
  assert.deepEqual(decodeRemoteAuthorityRequest(request), request);
  for (const field of ["now", "observed_at", "registered_agents", "principal_id", "next_projection", "runtime_root", "credential", "__proto__"]) {
    assert.throws(() => decodeRemoteAuthorityRequest({...request, [field]: "untrusted"}));
  }
  for (const patch of [{operation: "commitAuthority"}, {goal_id: ""}, {todo_id: "todo-invalid"}, {request_id: " a"}, {store_identity: "file:invalid"}]) {
    assert.throws(() => decodeRemoteAuthorityRequest({...request, ...patch}));
  }
  const {request_id: _, ...base} = request as ReturnType<typeof claimRequest> & {request_id: string};
  assert.throws(() => decodeRemoteAuthorityRequest({...base, operation: "renew", idempotency_key: "key", expected_version: 1, ttl_seconds: -1}));
});

test("policy binds credentials, tenant, Goal, actor, operation and incarnation before database access", async () => {
  const policy = decodeRemoteAuthorityPolicy(servicePolicy()), request = claimRequest("a");
  let connections = 0;
  const service = new RemoteAuthorityService({database: {connect: async () => {connections++; throw new Error("must not connect");}},
    admit: async (credential, command) => remoteAuthorityPolicyAdmission(policy, credential, command)});
  for (const patch of [{tenant_id: "tenant-two"}, {goal_id: "goal-two"}, {actor_id: "actor-b"},
    {store_identity: `postgresql:${"d".repeat(32)}`}]) {
    assert.equal((await service.dispatch(credentials.a, {...request, ...patch})).status, "rejected");
  }
  assert.equal((await service.dispatch(credentials.b, request)).status, "rejected");
  assert.equal((await service.dispatch("wrong", request)).reason_code, "principal_unauthenticated");
  const readOnly = servicePolicy(); readOnly.principals[0].grants[0].operations = ["read"];
  assert.equal(remoteAuthorityPolicyAdmission(decodeRemoteAuthorityPolicy(readOnly), credentials.a, request).status, "rejected");
  assert.equal(connections, 0);
});

for (const operation of ["renew", "release"] as const) {
  test(`remote ${operation} versions use the canonical non-negative safe-integer range`, () => {
    const {request_id: _, ...scope} = claimRequest("a") as ReturnType<typeof claimRequest> & {request_id: string};
    const request = {...scope, operation, idempotency_key: "existing-key", ...(operation === "renew" ? {ttl_seconds: 600} : {})};
    for (const expected_version of [0, 1, Number.MAX_SAFE_INTEGER]) {
      const valid = {...request, expected_version};
      assert.deepEqual(decodeRemoteAuthorityRequest(valid), valid);
    }
    for (const expected_version of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, Number.NaN, Infinity, "0", null, true]) {
      assert.throws(() => decodeRemoteAuthorityRequest({...request, expected_version}));
    }
  });
}

test("missing-lease release responses require explicit no-op evidence", () => {
  const {request_id: _, ...scope} = claimRequest("a") as ReturnType<typeof claimRequest> & {request_id: string};
  const request = decodeRemoteAuthorityRequest({...scope, operation: "release", idempotency_key: "existing-key", expected_version: 0});
  const result: JsonObject = {status: "no_change", changed: false, released: false, missing: true,
    handoff_mode: "hard_lease", provider_revision: `${request.store_identity}:3`, cursor: "3"};
  const wrap = (payload: JsonObject) => remoteAuthorityResponse(request, "execute", {
    status: "result", operation_id: remoteAuthorityOperationId(request, "principal-a"), result: payload});
  assert.deepEqual(decodeRemoteAuthorityResponse(wrap(result), request, "execute"), wrap(result));
  for (const field of ["missing", "released", "changed", "provider_revision", "cursor"]) {
    const incomplete = {...result}; delete incomplete[field];
    assert.throws(() => decodeRemoteAuthorityResponse(wrap(incomplete), request, "execute"));
  }
  for (const patch of [{released: true}, {changed: true}, {missing: false}, {lease: {}}, {status: "applied"}]) {
    assert.throws(() => decodeRemoteAuthorityResponse(wrap({...result, ...patch}), request, "execute"));
  }
});

test("invalid policy cannot silently broaden or retain grants", async t => {
  for (const mutate of [(p: ReturnType<typeof servicePolicy>) => p.principals.push(p.principals[0]),
    (p: ReturnType<typeof servicePolicy>) => p.scopes.push(p.scopes[0]),
    (p: ReturnType<typeof servicePolicy>) => p.principals[0].grants[0].operations.push("arbitrary-write"),
    (p: ReturnType<typeof servicePolicy>) => p.principals[0].grants[0].actor_id = "unregistered"]) {
    const policy = servicePolicy(); mutate(policy); assert.throws(() => decodeRemoteAuthorityPolicy(policy));
  }
  const root = await mkdtemp(join(tmpdir(), "loopx-remote-policy-")), path = join(root, "policy.json");
  t.after(() => rm(root, {recursive: true, force: true}));
  await writeFile(path, JSON.stringify(servicePolicy()));
  assert.deepEqual(await readRemoteAuthorityPolicy(path), servicePolicy());
  await writeFile(path, JSON.stringify(servicePolicy()) + " ".repeat(256 * 1024));
  await assert.rejects(readRemoteAuthorityPolicy(path), /too large/u);
});

test("client requires verified TLS and does not put credentials in URLs", async () => {
  for (const endpoint of ["http://localhost/", "https://user:secret@localhost/", "https://localhost/?credential=x", "https://localhost/path"]) {
    await assert.rejects(callRemoteAuthority(endpoint, credentials.a, claimRequest("a")));
  }
  assert.throws(() => createRemoteAuthorityServer({} as RemoteAuthorityService, {}));
});

test("TLS listener bounds input and admission, and drains admitted requests", async t => {
  const tls = await tlsFixture(t);
  let admissions = 0, release!: () => void, entered!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  const started = new Promise<void>(resolve => {entered = resolve;});
  const service = new RemoteAuthorityService({database: {connect: async () => {throw new Error("Unexpected database access");}},
    admit: async () => {admissions++; entered(); await gate; return {status: "rejected", reason_code: "fixture_denial"};}});
  const listener = createRemoteAuthorityServer(service, tls, 1);
  const endpoint = `https://127.0.0.1:${await listener.listen()}`;
  t.after(async () => {release(); if (listener.server.listening) await listener.close();});
  async function raw(body: string, headers: Record<string, string> = {}) {
    return new Promise<number>((resolve, reject) => {
      const outgoing = httpsRequest(new URL("/v0/tasks/execute", endpoint), {method: "POST", ca: tls.cert,
        headers: {Authorization: `Bearer ${credentials.a}`, "Content-Type": "application/json", ...headers}}, response => {
        response.resume(); response.on("end", () => resolve(response.statusCode!));
      });
      outgoing.on("error", reject); outgoing.end(body);
    });
  }
  assert.equal(await raw(JSON.stringify({...claimRequest("a"), now: "caller-clock"})), 400);
  const {request_id: _, ...scope} = claimRequest("a") as ReturnType<typeof claimRequest> & {request_id: string};
  for (const operation of ["renew", "release"]) {
    assert.equal(await raw(JSON.stringify({...scope, operation, idempotency_key: "existing-key", expected_version: -1,
      ...(operation === "renew" ? {ttl_seconds: 600} : {})})), 400);
  }
  assert.equal(await raw("x".repeat(17 * 1024)), 413);
  assert.equal(await raw(JSON.stringify(claimRequest("a")), {Origin: "https://unrelated.invalid"}), 401);
  assert.equal(admissions, 0);
  const pending = callRemoteAuthority(endpoint, credentials.a, claimRequest("a"), {ca: tls.cert});
  await started;
  assert.equal((await callRemoteAuthority(endpoint, credentials.a, claimRequest("a"), {ca: tls.cert})).status, "ambiguous");
  assert.equal(admissions, 1);
  let closed = false;
  const stopping = listener.close().then(() => {closed = true;});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(closed, false);
  release(); assert.equal((await pending).reason_code, "fixture_denial");
  await stopping; assert.equal(closed, true);
});

test("shutdown bounds incomplete connections while still waiting for dispatched work", {timeout: 20000}, async t => {
  const tls = await tlsFixture(t);
  let admissions = 0, entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => {entered = resolve;});
  const gate = new Promise<void>(resolve => {release = resolve;});
  const service = new RemoteAuthorityService({database: {connect: async () => {throw new Error("Unexpected database access");}},
    admit: async () => {admissions++; entered(); await gate; return {status: "rejected", reason_code: "fixture_denial"};}});
  const listener = createRemoteAuthorityServer(service, tls, 4), port = await listener.listen();
  const sockets: Socket[] = [];
  let stopping: Promise<void> | undefined;
  t.after(async () => {release(); sockets.forEach(socket => socket.destroy()); await (stopping ?? listener.close());});
  const own = <T extends Socket>(socket: T) => {sockets.push(socket); socket.on("error", () => {}); socket.resume(); return socket;};
  // A peer can stall before TLS, before the HTTP header terminator, or in the
  // body. None of these peers has submitted a complete command to dispatch.
  const handshake = own(connect({host: "127.0.0.1", port}));
  await once(handshake, "connect");
  const headers = own(tlsConnect({host: "127.0.0.1", port, ca: tls.cert}));
  await once(headers, "secureConnect");
  headers.write("POST /v0/tasks/execute HTTP/1.1\r\nHost: localhost\r\n");
  const body = own(tlsConnect({host: "127.0.0.1", port, ca: tls.cert}));
  await once(body, "secureConnect");
  const receiving = once(listener.server, "request");
  body.write(`POST /v0/tasks/execute HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer ${credentials.a}\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{`);
  await receiving;
  const pending = callRemoteAuthority(`https://127.0.0.1:${port}`, credentials.a, claimRequest("a"), {ca: tls.cert});
  await started;
  let closed = false;
  const peersClosed = Promise.all(sockets.map(socket => new Promise<void>(resolve => socket.once("close", resolve))));
  stopping = listener.close().then(() => {closed = true;});
  let deadline: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([peersClosed, new Promise<never>((_, reject) => {
      deadline = setTimeout(() => reject(new Error("incomplete peers outlived shutdown drain")), 10000);
    })]);
    // A lost socket is not a rollback: close must retain the database pool
    // until the already-dispatched service operation has actually settled.
    assert.equal(closed, false);
    assert.equal(admissions, 1);
    release();
    assert.equal((await pending).status, "ambiguous");
    await stopping;
    assert.equal(closed, true);
  } finally {clearTimeout(deadline);}
});

test("a body completed after shutdown starts cannot dispatch a new command", async t => {
  const tls = await tlsFixture(t);
  let admissions = 0;
  const service = new RemoteAuthorityService({database: {connect: async () => {throw new Error("Unexpected database access");}},
    admit: async () => {admissions++; return {status: "rejected", reason_code: "fixture_denial"};}});
  const listener = createRemoteAuthorityServer(service, tls), port = await listener.listen();
  const socket = tlsConnect({host: "127.0.0.1", port, ca: tls.cert});
  socket.on("error", () => {}); socket.resume();
  let stopping: Promise<void> | undefined;
  t.after(async () => {socket.destroy(); await (stopping ?? listener.close());});
  await once(socket, "secureConnect");
  const body = JSON.stringify(claimRequest("a")), receiving = once(listener.server, "request");
  socket.write(`POST /v0/tasks/execute HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer ${credentials.a}\r\nContent-Type: application/json\r\nContent-Length: ${Buffer.byteLength(body)}\r\nConnection: close\r\n\r\n${body.slice(0, 1)}`);
  await receiving;
  stopping = listener.close();
  socket.write(body.slice(1));
  await stopping;
  assert.equal(admissions, 0);
});

test("complete, block, defer, todo_add and snapshot request decoding validates typed lifecycle semantics", () => {
  const base = {
    schema_version: "loopx_remote_authority_request_v0" as const,
    tenant_id: "tenant-one",
    goal_id: "goal-one",
    actor_id: "actor-a",
    store_identity: `postgresql:${"c".repeat(32)}`,
    todo_id: "todo_0aaf8c3edda601ecc3c5b8c0",
  };

  // Complete
  const completeReq = {
    ...base,
    operation: "complete" as const,
    request_id: "req-complete-1",
    claim_request_id: "req-claim-1",
    evidence: "sha256:abcd verified delivery receipt",
    note: "optional completion note",
  };
  assert.deepEqual(decodeRemoteAuthorityRequest(completeReq), completeReq);
  assert.throws(() => decodeRemoteAuthorityRequest({...completeReq, evidence: ""}));
  assert.throws(() => decodeRemoteAuthorityRequest({...completeReq, claim_request_id: ""}));

  // Block
  const blockReq = {
    ...base,
    operation: "block" as const,
    request_id: "req-block-1",
    reason: "waiting on upstream schema",
    resume_when: "schema_applied",
    note: "paused temporarily",
  };
  assert.deepEqual(decodeRemoteAuthorityRequest(blockReq), blockReq);
  assert.throws(() => decodeRemoteAuthorityRequest({...blockReq, reason: ""}));

  // Defer
  const deferReq = {
    ...base,
    operation: "defer" as const,
    request_id: "req-defer-1",
    reason: "resuming next week",
    resume_when: "resume_at:2026-11-20T00:00:00Z",
  };
  assert.deepEqual(decodeRemoteAuthorityRequest(deferReq), deferReq);
  assert.throws(() => decodeRemoteAuthorityRequest({...deferReq, resume_when: ""}));

  // Todo add
  const addReq = {
    ...base,
    operation: "todo_add" as const,
    request_id: "req-add-1",
    text: "[P1] new remote task",
    priority: "P1",
    role: "agent" as const,
    task_class: "advancement_task",
    required_write_scopes: ["loopx/control_plane/**"],
  };
  assert.deepEqual(decodeRemoteAuthorityRequest(addReq), addReq);
  assert.throws(() => decodeRemoteAuthorityRequest({...addReq, priority: "P9"}));
  assert.throws(() => decodeRemoteAuthorityRequest({...addReq, role: "invalid" as any}));

  // Snapshot (todo_id is optional)
  const snapshotGoal = {
    schema_version: "loopx_remote_authority_request_v0" as const,
    tenant_id: "tenant-one",
    goal_id: "goal-one",
    actor_id: "actor-a",
    store_identity: `postgresql:${"c".repeat(32)}`,
    operation: "snapshot" as const,
  };
  assert.deepEqual(decodeRemoteAuthorityRequest(snapshotGoal), snapshotGoal);
  const snapshotTodo = {...snapshotGoal, todo_id: "todo_0aaf8c3edda601ecc3c5b8c0"};
  assert.deepEqual(decodeRemoteAuthorityRequest(snapshotTodo), snapshotTodo);
});

test("policy admits complete, block, defer, todo_add and snapshot grants and rejects ungranted operations", () => {
  const policy = servicePolicy();
  const grant = policy.principals[0].grants[0];
  grant.operations = ["read", "claim", "progress", "renew", "release", "complete", "block", "defer", "todo_add", "snapshot"];
  const decodedPolicy = decodeRemoteAuthorityPolicy(policy);

  const base = {
    schema_version: "loopx_remote_authority_request_v0" as const,
    tenant_id: "tenant-one",
    goal_id: "goal-one",
    actor_id: "actor-a",
    store_identity: `postgresql:${"c".repeat(32)}`,
    todo_id: "todo_one",
  };

  const completeReq = decodeRemoteAuthorityRequest({
    ...base, operation: "complete", request_id: "req-comp", claim_request_id: "req-claim", evidence: "evidence-1",
  });
  const snapshotReq = decodeRemoteAuthorityRequest({
    ...base, operation: "snapshot",
  });

  const admComplete = remoteAuthorityPolicyAdmission(decodedPolicy, credentials.a, completeReq);
  assert.equal(admComplete.status, "allowed");

  const admSnapshot = remoteAuthorityPolicyAdmission(decodedPolicy, credentials.a, snapshotReq);
  assert.equal(admSnapshot.status, "allowed");

  // Default-deny check: principal b does not have "complete" granted
  policy.principals[1].grants[0].operations = ["read", "claim"];
  const decodedPolicyWithRestrictedB = decodeRemoteAuthorityPolicy(policy);
  assert.equal(policy.principals[1].grants[0].operations.includes("complete" as any), false);
  const admDenied = remoteAuthorityPolicyAdmission(decodedPolicyWithRestrictedB, credentials.b, {
    ...completeReq, actor_id: "actor-b",
  });
  assert.equal(admDenied.status, "rejected");
  assert.equal(admDenied.reason_code, "remote_authority_scope_denied");
});

test("decodeRemoteAuthorityResponse validates snapshot and complete response payloads", () => {
  const base = {
    schema_version: "loopx_remote_authority_request_v0" as const,
    tenant_id: "tenant-one",
    goal_id: "goal-one",
    actor_id: "actor-a",
    store_identity: `postgresql:${"c".repeat(32)}`,
    todo_id: "todo_one",
  };
  const snapshotReq = decodeRemoteAuthorityRequest({...base, operation: "snapshot"});
  const snapResponse = remoteAuthorityResponse(snapshotReq, "execute", {
    status: "snapshot",
    provider_revision: `${base.store_identity}:10`,
    store_identity: base.store_identity,
    goal_id: base.goal_id,
    receipt_sha256: "a".repeat(64),
    todos: [{todo_id: "todo_one", status: "open", role: "agent"}],
    leases: [],
  });
  assert.deepEqual(decodeRemoteAuthorityResponse(snapResponse, snapshotReq, "execute"), snapResponse);

  const completeReq = decodeRemoteAuthorityRequest({
    ...base, operation: "complete", request_id: "req-c", claim_request_id: "req-cl", evidence: "proof",
  });
  const opId = remoteAuthorityOperationId(completeReq, "principal-a");
  const compResponse = remoteAuthorityResponse(completeReq, "execute", {
    status: "result",
    operation_id: opId,
    result: {
      status: "applied",
      changed: true,
      completed: true,
      todo_id: "todo_one",
      provider_revision: `${base.store_identity}:11`,
      cursor: "11",
    },
  });
  assert.deepEqual(decodeRemoteAuthorityResponse(compResponse, completeReq, "execute"), compResponse);
});
