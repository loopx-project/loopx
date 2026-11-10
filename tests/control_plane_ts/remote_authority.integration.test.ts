import assert from "node:assert/strict";
import test from "node:test";
import {createServer, request as httpsRequest} from "node:https";
import type {AddressInfo} from "node:net";
import {writeFile} from "node:fs/promises";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {RemoteAuthorityService} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {fileRemoteAuthorityAdmission} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {callRemoteAuthority} from "../../loopx/control_plane/coordination/remote_authority_http.ts";
import {rotatePostgreSqlAuthorityStoreIdentity, type PostgreSqlAuthorityDatabase} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {remoteAuthorityOperationId, type RemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {postgresRemoteFixture, claimRequest, credentials, serviceIdentity, servicePolicy} from "./remote_authority_fixture.ts";

const connectionString = process.env.LOOPX_TEST_POSTGRES_URL;
test("remote authority real TLS/PostgreSQL task journey and recovery", {skip: !connectionString}, async t => {
  const fixture = await postgresRemoteFixture(t, connectionString!);
  let running = await fixture.start();
  const call = (request: RemoteAuthorityRequest, mode: "execute" | "recover" = "execute", credential = credentials.a) =>
    callRemoteAuthority(running.endpoint, credential, request, {ca: fixture.cert, mode});
  const result = (value: JsonObject) => value.result as JsonObject;
  const lease = (value: JsonObject) => result(value).lease as JsonObject;
  const base = (todo: string) => {
    const {operation: _, request_id: __, ...scope} = claimRequest("a", todo) as Extract<RemoteAuthorityRequest, {operation: "claim"}>;
    return scope;
  };
  await t.test("independent CLI clients have exactly one complete winner for one task", async () => {
    await fixture.seed(["todo_race"]);
    const responses = await Promise.all([fixture.client(running.endpoint, "a", claimRequest("a", "todo_race")),
      fixture.client(running.endpoint, "b", claimRequest("b", "todo_race"))]);
    assert.equal(responses.filter(r => result(r.value).status === "applied").length, 1);
    assert.equal(responses.filter(r => r.code === 0).length, 1);
    const head = await fixture.store.loadAuthority(); assert.equal(head.status, "loaded");
    if (head.status !== "loaded") return;
    assert.equal((head.head.leases as JsonObject[]).length, 1);
    const receipts = await Promise.all(["a", "b"].map(actor => fixture.store.readReceipt(
      remoteAuthorityOperationId(claimRequest(actor as "a" | "b", "todo_race"), `principal-${actor}`))));
    assert.equal(receipts.filter(r => r.status === "found").length, 1);
  });
  await t.test("independent clients keep both unrelated tasks", async () => {
    await fixture.seed(["todo_left", "todo_right"]);
    const responses = await Promise.all([fixture.client(running.endpoint, "a", claimRequest("a", "todo_left")),
      fixture.client(running.endpoint, "b", claimRequest("b", "todo_right"))]);
    assert(responses.every(r => r.code === 0 && result(r.value).status === "applied"));
  });
  await t.test("concurrent identical commands return one durable outcome", async () => {
    await fixture.seed(["todo_retry"]);
    const request = claimRequest("a", "todo_retry");
    const responses = await Promise.all([fixture.client(running.endpoint, "a", request),
      fixture.client(running.endpoint, "a", request)]);
    assert(responses.every(r => r.code === 0));
    assert.equal(responses.filter(r => result(r.value).status === "applied").length, 1);
    const receipt = await fixture.store.readReceipt(remoteAuthorityOperationId(request, "principal-a"));
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") assert.equal(receipt.receipts.length, 1);
    assert.deepEqual(lease(responses[0].value), lease(responses[1].value));
  });
  await t.test("claim, progress, renew, release and readback use the existing task owners", async () => {
    await fixture.seed(["todo_journey"]);
    const request = claimRequest("a", "todo_journey");
    const claimed = await call(request); assert.equal(result(claimed).status, "applied");
    const progress: RemoteAuthorityRequest = {...base("todo_journey"), operation: "progress", request_id: "progress-one",
      claim_request_id: "claim-todo_journey", note: "Validated the requested artifact"};
    assert.equal(result(await call(progress)).status, "applied");
    const beforeReplay = await fixture.store.loadAuthority();
    assert.equal(result(await call(progress)).status, "replayed");
    assert.deepEqual(await fixture.store.loadAuthority(), beforeReplay);
    assert.equal((await call({...progress, note: "Different operation intent"})).reason_code, "remote_authority_operation_mismatch");
    const renewed = await call({...base("todo_journey"), operation: "renew", idempotency_key: String(lease(claimed).idempotency_key),
      expected_version: Number(lease(claimed).version), ttl_seconds: 600});
    assert.equal(result(renewed).status, "applied", JSON.stringify(renewed));
    const followupNote = "Validated readback after lease renewal";
    assert.equal(result(await call({...progress, request_id: "progress-two", note: followupNote})).status, "applied");
    const release: RemoteAuthorityRequest = {...base("todo_journey"), operation: "release",
      idempotency_key: String(lease(renewed).idempotency_key), expected_version: Number(lease(renewed).version)};
    const released = await call(release); assert.equal(result(released).status, "applied");
    assert.equal(result(await call(release)).status, "replayed");
    const read = await call({...base("todo_journey"), operation: "read"});
    assert.equal((read.todo as JsonObject).note, followupNote);
    assert.equal((read.lease as JsonObject).status, "released");
    assert.equal(Object.hasOwn(read.lease as JsonObject, "idempotency_key"), false);
    assert.equal((await call(release, "recover")).historical_only, true);
  });
  await t.test("bad credential and wrong tenant/Goal/actor fail without a write", async () => {
    const before = await fixture.store.loadAuthority();
    for (const patch of [{tenant_id: "tenant-other"}, {goal_id: "goal-other"}, {actor_id: "actor-b"}]) {
      assert.equal((await call({...claimRequest("a"), ...patch})).status, "rejected");
    }
    assert.equal((await call(claimRequest("a"), "execute", `invalid-${"x".repeat(48)}`)).reason_code, "principal_unauthenticated");
    assert.deepEqual(await fixture.store.loadAuthority(), before);
  });
  await t.test("revocation and unreadable policy reject new admissions, including cached TLS connections", async () => {
    const policy = servicePolicy(); policy.principals = policy.principals.slice(1);
    await fixture.writePolicy(policy);
    const before = await fixture.store.loadAuthority();
    assert.equal((await call(claimRequest("a"))).reason_code, "principal_unauthenticated");
    await writeFile(fixture.policyPath, "not JSON");
    assert.equal((await call(claimRequest("a"))).reason_code, "remote_authority_policy_unavailable");
    assert.deepEqual(await fixture.store.loadAuthority(), before);
    await fixture.writePolicy();
  });
  await t.test("lost response and server restart recover the exact receipt without repeating the write", async () => {
    await fixture.seed(["todo_disconnect"]);
    const original = claimRequest("a", "todo_disconnect");
    // Relay the real TLS request, wait for the complete committed response, then
    // deliberately discard it. No mocked database or fabricated receipt.
    const proxy = createServer({key: fixture.key, cert: fixture.cert}, (incoming, outgoing) => {
      const upstream = httpsRequest(new URL(incoming.url!, running.endpoint), {method: "POST", ca: fixture.cert,
        headers: incoming.headers}, response => {response.resume(); response.on("end", () => outgoing.destroy());});
      upstream.on("error", () => outgoing.destroy()); incoming.pipe(upstream);
    });
    await new Promise<void>(resolve => proxy.listen(0, "127.0.0.1", resolve));
    let lost: JsonObject;
    try {lost = await callRemoteAuthority(`https://127.0.0.1:${(proxy.address() as AddressInfo).port}`, credentials.a, original, {ca: fixture.cert});}
    finally {await new Promise<void>(resolve => proxy.close(() => resolve()));}
    assert.equal(lost.status, "ambiguous");
    const receipt = await fixture.store.readReceipt(remoteAuthorityOperationId(original, "principal-a"));
    assert.equal(receipt.status, "found"); if (receipt.status !== "found") return;
    const before = await fixture.store.loadAuthority();
    const port = running.port; await running.stop(); running = await fixture.start(port);
    const recovered = await fixture.client(running.endpoint, "a", original, true);
    assert.equal(recovered.code, 0); assert.equal(recovered.value.status, "found");
    assert.equal(recovered.value.receipt_sha256, canonicalAuthoritySha256(receipt.receipts[0]));
    assert.equal(result(await call(original)).status, "replayed");
    assert.deepEqual(await fixture.store.loadAuthority(), before);
  });
  await t.test("unknown receipt query is read-only and does not turn into a claim", async () => {
    const before = await fixture.store.loadAuthority();
    assert.equal((await call(claimRequest("a", "todo_never-created", "unknown"), "recover")).status, "missing");
    assert.deepEqual(await fixture.store.loadAuthority(), before);
  });
  await t.test("admitted work completes truthfully when its credential is revoked in flight", async () => {
    await fixture.seed(["todo_admitted"]);
    let release!: () => void, admitted!: () => void;
    const gate = new Promise<void>(resolve => {release = resolve;});
    const started = new Promise<void>(resolve => {admitted = resolve;});
    const admission = fileRemoteAuthorityAdmission(fixture.policyPath);
    const service = new RemoteAuthorityService({database: fixture.runtimeDatabase, admit: async (credential, request) => {
      const snapshot = await admission(credential, request); admitted(); await gate; return snapshot;
    }});
    const pending = service.dispatch(credentials.a, claimRequest("a", "todo_admitted"));
    await started;
    const policy = servicePolicy(); policy.principals = policy.principals.slice(1); await fixture.writePolicy(policy);
    release();
    assert.equal(result(await pending).status, "applied");
    assert.equal((await call(claimRequest("a", "todo_admitted"))).status, "rejected");
    await fixture.writePolicy();
  });
  await t.test("server clock and canonical lease fence prevent stale execution after takeover", async () => {
    await fixture.seed(["todo_takeover"]);
    let now = new Date("2026-11-10T10:00:00Z");
    const service = new RemoteAuthorityService({database: fixture.runtimeDatabase,
      admit: fileRemoteAuthorityAdmission(fixture.policyPath), now: () => now});
    const first = claimRequest("a", "todo_takeover");
    assert.equal(result(await service.dispatch(credentials.a, first)).status, "applied");
    now = new Date("2026-11-11T10:00:00Z");
    assert.equal(result(await service.dispatch(credentials.b, claimRequest("b", "todo_takeover"))).status, "applied");
    const before = await fixture.store.loadAuthority();
    const late: RemoteAuthorityRequest = {...base("todo_takeover"), operation: "progress", request_id: "stale-progress",
      claim_request_id: "claim-todo_takeover", note: "Must not be written"};
    const stale = await service.dispatch(credentials.a, late);
    assert.equal(result(stale).status, "failed");
    assert.equal(result(await service.dispatch(credentials.a, first)).status, "failed");
    assert.deepEqual(await fixture.store.loadAuthority(), before);
    assert.equal((await service.dispatch(credentials.a, first, "recover")).historical_only, true);
  });
  await t.test("rotation between opening and reading cannot let a stale session adopt a new incarnation", async () => {
    await fixture.seed(["todo_rotation"]);
    let reached!: () => void, release!: () => void, first = true;
    const paused = new Promise<void>(resolve => {reached = resolve;});
    const gate = new Promise<void>(resolve => {release = resolve;});
    const delayed: PostgreSqlAuthorityDatabase = {connect: async () => {
      const connection = await fixture.runtimeDatabase.connect();
      return {...connection, query: async (sql, args) => {
        const result = await connection.query(sql, args);
        if (first && sql.includes("authority_store_metadata")) {first = false; reached(); await gate;}
        return result;
      }};
    }};
    const service = new RemoteAuthorityService({database: delayed, admit: fileRemoteAuthorityAdmission(fixture.policyPath)});
    const before = await fixture.store.loadAuthority();
    const pending = service.dispatch(credentials.a, claimRequest("a", "todo_rotation"));
    await paused;
    const next = `postgresql:${"d".repeat(32)}`;
    try {assert.equal((await rotatePostgreSqlAuthorityStoreIdentity(fixture.database, serviceIdentity, next)).status, "rotated");}
    finally {release();}
    assert.equal((await pending).status, "unavailable");
    const after = await fixture.store.loadAuthority();
    assert.equal(after.status, "loaded");
    if (before.status === "loaded" && after.status === "loaded") assert.deepEqual(after.head, before.head);
    assert.equal((await call(claimRequest("a", "todo_rotation"))).status, "unavailable");
    await fixture.writePolicy(servicePolicy(next));
    assert.equal((await call(claimRequest("a", "todo_rotation"))).status, "rejected");
    const historic = {...claimRequest("a", "todo_disconnect"), store_identity: next};
    assert.equal((await call(historic, "recover")).historical_only, true);
    assert.equal(result(await call({...claimRequest("a", "todo_rotation"), store_identity: next})).status, "applied");
  });
  await t.test("TLS verification failures never trigger a retry or expose a credential", async () => {
    const before = await fixture.store.loadAuthority();
    const failed = await callRemoteAuthority(running.endpoint, credentials.a, claimRequest("a"), {timeoutMs: 1000});
    assert.equal(failed.status, "ambiguous");
    assert(!JSON.stringify(failed).includes(credentials.a));
    assert.deepEqual(await fixture.store.loadAuthority(), before);
  });
});
