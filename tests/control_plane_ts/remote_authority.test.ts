import assert from "node:assert/strict";
import test from "node:test";
import {request as httpsRequest} from "node:https";
import {mkdtemp, writeFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {decodeRemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
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
