import assert from "node:assert/strict";
import test from "node:test";
import {createServer, request as httpsRequest} from "node:https";
import type {AddressInfo} from "node:net";
import {writeFile} from "node:fs/promises";
import {Client} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {RemoteAuthorityService} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {fileRemoteAuthorityAdmission} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {callRemoteAuthority, createRemoteAuthorityServer} from "../../loopx/control_plane/coordination/remote_authority_http.ts";
import {PostgreSqlAuthorityStore, rotatePostgreSqlAuthorityStoreIdentity, type PostgreSqlAuthorityDatabase} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {remoteAuthorityOperationId, type RemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {postgresRemoteFixture, claimRequest, credentials, serviceIdentity, servicePolicy} from "./remote_authority_fixture.ts";
import {registerRemoteAuthorityModeConformance} from "./remote_authority_mode_conformance.ts";

const connectionString = process.env.LOOPX_TEST_POSTGRES_URL;
registerRemoteAuthorityModeConformance(connectionString);
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
    const staleRelease: RemoteAuthorityRequest = {...base("todo_journey"), operation: "release",
      idempotency_key: String(lease(renewed).idempotency_key), expected_version: Number(lease(renewed).version)};
    // Normal progress may maintain its active execution, advancing the version.
    // The transport must not silently rebase a stale lifecycle command.
    const beforeRelease = await fixture.store.loadAuthority();
    assert.equal(result(await call(staleRelease)).status, "failed");
    assert.deepEqual(await fixture.store.loadAuthority(), beforeRelease);
    const current = (await call({...base("todo_journey"), operation: "read"})).lease as JsonObject;
    assert.equal(current.owner, "actor-a");
    assert.equal(current.lease_epoch, lease(renewed).lease_epoch);
    assert(Number(current.version) > Number(lease(renewed).version));
    const release: RemoteAuthorityRequest = {...staleRelease, expected_version: Number(current.version)};
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
  await t.test("CLI reads current lease facts and releases tasks regardless of note size", async () => {
    const notes = {todo_short_note: "s".repeat(1024), todo_long_note: "l".repeat(66560)};
    await fixture.seed(Object.keys(notes), notes);
    for (const [todo, note] of Object.entries(notes)) {
      const unclaimed = await fixture.client(running.endpoint, "a", {...base(todo), operation: "read"});
      assert.equal(unclaimed.code, 0); assert.equal(unclaimed.value.lease, null);
      const claimed = await fixture.client(running.endpoint, "a", claimRequest("a", todo));
      assert.equal(claimed.code, 0);
      if (todo === "todo_short_note") {
        // Existing canonical records also admit decimal-string lease counters.
        // The wire projection keeps them usable as numeric command arguments.
        const head = await fixture.store.loadAuthority(); assert.equal(head.status, "loaded");
        if (head.status !== "loaded") return;
        const projection = structuredClone(head.head);
        const original = (projection.leases as JsonObject[]).find(row => row.todo_id === todo)!;
        original.version = String(original.version); original.lease_epoch = String(original.lease_epoch);
        assert.equal((await fixture.store.commitAuthority({operation_id: "legacy-lease-counters",
          expected_provider_revision: head.provider_revision, next_projection: projection, events: [], receipts: []})).status, "applied");
      }
      const before = await fixture.store.loadAuthority();
      const read = await fixture.client(running.endpoint, "a", {...base(todo), operation: "read"});
      assert.equal(read.code, 0, "a valid note must not hide the lease needed for release");
      assert.equal(read.value.status, "found");
      const current = read.value.lease as JsonObject;
      assert.equal(current.owner, "actor-a"); assert.equal(current.status, "active");
      assert.equal(current.version, lease(claimed.value).version);
      assert.equal(current.lease_epoch, lease(claimed.value).lease_epoch);
      const projected = read.value.todo as JsonObject;
      if (todo === "todo_short_note") assert.equal(projected.note, note);
      else {assert.equal(projected.note_omitted, true); assert.equal(Object.hasOwn(projected, "note"), false);}
      assert(Buffer.byteLength(JSON.stringify(read.value)) <= 64 * 1024);
      assert.deepEqual(await fixture.store.loadAuthority(), before);
      const released = await fixture.client(running.endpoint, "a", {...base(todo), operation: "release",
        idempotency_key: String(lease(claimed.value).idempotency_key), expected_version: Number(current.version)});
      assert.equal(released.code, 0); assert.equal(lease(released.value).status, "released");
    }
  });
  await t.test("CLI rejects a correlated but incomplete success and recovers the original intent", async () => {
    await fixture.seed(["todo_incomplete_reply"]);
    const request = claimRequest("a", "todo_incomplete_reply");
    let reply: JsonObject = {schema_version: "loopx_remote_authority_response_v0",
      request_sha256: canonicalAuthoritySha256({mode: "execute", request}), status: "result", result: {status: "applied"}};
    const proxy = createServer({key: fixture.key, cert: fixture.cert}, (incoming, outgoing) => {
      incoming.resume(); outgoing.setHeader("Content-Type", "application/json"); outgoing.end(JSON.stringify(reply));
    });
    await new Promise<void>(resolve => proxy.listen(0, "127.0.0.1", resolve));
    try {
      const endpoint = `https://127.0.0.1:${(proxy.address() as AddressInfo).port}`;
      const malformed = await fixture.client(endpoint, "a", request);
      assert.equal(malformed.code, 1, "request correlation alone does not establish a completed claim");
      assert.equal(malformed.value.status, "ambiguous");
      reply = {...reply, request_sha256: "0".repeat(64)};
      assert.equal((await fixture.client(endpoint, "a", request)).code, 1);
      const recovered = await fixture.client(running.endpoint, "a", request, true);
      assert.equal(recovered.code, 0); assert.equal(recovered.value.status, "missing");
      const valid = await fixture.client(running.endpoint, "a", request);
      assert.equal(valid.code, 0); assert.equal(lease(valid.value).owner, "actor-a");
      reply = valid.value;
      assert.equal((await fixture.client(endpoint, "a", request)).code, 0);

      const progress: RemoteAuthorityRequest = {...base(request.todo_id), operation: "progress",
        request_id: "verified-progress", claim_request_id: `claim-${request.todo_id}`, note: "Validated response evidence"};
      const progressed = await fixture.client(running.endpoint, "a", progress); assert.equal(progressed.code, 0);
      const read: RemoteAuthorityRequest = {...base(request.todo_id), operation: "read"};
      const current = await fixture.client(running.endpoint, "a", read); assert.equal(current.code, 0);
      const renew: RemoteAuthorityRequest = {...base(request.todo_id), operation: "renew", ttl_seconds: 600,
        idempotency_key: String(lease(valid.value).idempotency_key), expected_version: Number((current.value.lease as JsonObject).version)};
      const renewed = await fixture.client(running.endpoint, "a", renew); assert.equal(renewed.code, 0);
      const release: RemoteAuthorityRequest = {...base(request.todo_id), operation: "release",
        idempotency_key: renew.idempotency_key, expected_version: Number(lease(renewed.value).version)};
      const released = await fixture.client(running.endpoint, "a", release); assert.equal(released.code, 0);
      const cases: {request: RemoteAuthorityRequest; reply: JsonObject; required: string[]}[] = [
        {request, reply: valid.value, required: ["operation_id", "result.lease", "result.claim_operation_id", "result.claimed_by"]},
        {request: progress, reply: progressed.value, required: ["operation_id", "result.todo_id", "result.changed", "result.cursor"]},
        {request: renew, reply: renewed.value, required: ["result.renewed", "result.lease.version", "result.lease.idempotency_key"]},
        {request: release, reply: released.value, required: ["result.released", "result.lease", "result.provider_revision"]},
        {request: read, reply: current.value, required: ["todo", "lease", "lease.owner", "lease.lease_epoch", "lease.version"]},
      ];
      function removeField(value: JsonObject, path: string) {
        const keys = path.split("."), last = keys.pop()!;
        let parent = value;
        for (const key of keys) parent = parent[key] as JsonObject;
        assert(Object.hasOwn(parent, last), path); delete parent[last];
      }
      for (const entry of cases) {
        reply = entry.reply;
        assert.equal((await fixture.client(endpoint, "a", entry.request)).code, 0, entry.request.operation);
        for (const field of entry.required) {
          reply = structuredClone(entry.reply); removeField(reply, field);
          const invalid = await fixture.client(endpoint, "a", entry.request);
          assert.equal(invalid.code, 1, `${entry.request.operation}: missing ${field}`);
          assert.equal(invalid.value.status, entry.request.operation === "read" ? "unavailable" : "ambiguous");
        }
        if (entry.request.operation === "read") continue;
        // Historical receipt payloads have no execution status and must remain
        // readable after release; they cannot be promoted to current authority.
        const history = await fixture.client(running.endpoint, "a", entry.request, true);
        assert.equal(history.code, 0); assert.equal(history.value.historical_only, true);
        reply = history.value;
        assert.equal((await fixture.client(endpoint, "a", entry.request, true)).code, 0);
        for (const field of ["operation_id", "receipt_sha256", "historical_only", "result"]) {
          reply = structuredClone(history.value); removeField(reply, field);
          const invalid = await fixture.client(endpoint, "a", entry.request, true);
          assert.equal(invalid.code, 1, `recovery: missing ${field}`); assert.equal(invalid.value.status, "unavailable");
        }
      }
      for (const patch of [
        (value: JsonObject) => {result(value).status = ["applied"];},
        (value: JsonObject) => {result(value).todo_id = "todo_other";},
        (value: JsonObject) => {lease(value).owner = "actor-b";},
        (value: JsonObject) => {lease(value).version = "1";},
        (value: JsonObject) => {lease(value).expires_at = "2026-02-31T00:00:00Z";},
        (value: JsonObject) => {value.status = "missing";},
      ]) {
        reply = structuredClone(valid.value); patch(reply);
        assert.equal((await fixture.client(endpoint, "a", request)).code, 1);
      }
    } finally {await new Promise<void>(resolve => proxy.close(() => resolve()));}
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
  await t.test("shutdown waits for an admitted transaction after dropping its response socket", {timeout: 20000}, async t => {
    await fixture.seed(["todo_shutdown"]);
    let reached!: () => void, release!: () => void;
    const committing = new Promise<void>(resolve => {reached = resolve;});
    const gate = new Promise<void>(resolve => {release = resolve;});
    const delayed: PostgreSqlAuthorityDatabase = {connect: async () => {
      const connection = await fixture.runtimeDatabase.connect();
      return {...connection, query: async (sql, args) => {
        if (sql === "COMMIT") {reached(); await gate;}
        return connection.query(sql, args);
      }};
    }};
    const service = new RemoteAuthorityService({database: delayed, admit: fileRemoteAuthorityAdmission(fixture.policyPath)});
    const listener = createRemoteAuthorityServer(service, fixture);
    const endpoint = `https://127.0.0.1:${await listener.listen()}`;
    let stopping: Promise<void> | undefined, closed = false;
    t.after(async () => {release(); await (stopping ?? listener.close());});
    const original = claimRequest("a", "todo_shutdown");
    const pending = callRemoteAuthority(endpoint, credentials.a, original, {ca: fixture.cert});
    await committing;
    stopping = listener.close().then(() => {closed = true;});
    assert.equal((await pending).status, "ambiguous");
    assert.equal(closed, false, "the database must stay available until COMMIT settles");
    release(); await stopping;
    const receipt = await fixture.store.readReceipt(remoteAuthorityOperationId(original, "principal-a"));
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") {
      const recovered = await call(original, "recover");
      assert.equal(recovered.status, "found");
      assert.equal(recovered.receipt_sha256, canonicalAuthoritySha256(receipt.receipts[0]));
    }
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
    let reached!: () => void, release!: () => void, opened = 0;
    const paused = new Promise<void>(resolve => {reached = resolve;});
    const gate = new Promise<void>(resolve => {release = resolve;});
    const delayed: PostgreSqlAuthorityDatabase = {connect: async () => {
      const connection = await fixture.runtimeDatabase.connect();
      return {...connection, query: async (sql, args) => {
        const result = await connection.query(sql, args);
        if (opened < 2 && sql.includes("authority_store_metadata")) {
          opened++; if (opened === 2) reached(); await gate;
        }
        return result;
      }};
    }};
    const service = new RemoteAuthorityService({database: delayed, admit: fileRemoteAuthorityAdmission(fixture.policyPath)});
    const before = await fixture.store.loadAuthority();
    const pending = service.dispatch(credentials.a, claimRequest("a", "todo_rotation"));
    const recovering = service.dispatch(credentials.a, claimRequest("a", "todo_rotation", "unknown-operation"), "recover");
    await paused;
    const next = `postgresql:${"d".repeat(32)}`;
    try {assert.equal((await rotatePostgreSqlAuthorityStoreIdentity(fixture.database, serviceIdentity, next)).status, "rotated");}
    finally {release();}
    assert.equal((await pending).status, "unavailable");
    const recovery = await recovering;
    assert.equal(recovery.status, "unavailable", "negative receipt evidence must belong to the admitted incarnation");
    assert.equal(recovery.reason_code, "remote_authority_incarnation_changed");
    const after = await fixture.store.loadAuthority();
    assert.equal(after.status, "loaded");
    if (before.status === "loaded" && after.status === "loaded") assert.deepEqual(after.head, before.head);
    assert.equal((await call(claimRequest("a", "todo_rotation"))).status, "unavailable");
    await fixture.writePolicy(servicePolicy(next));
    assert.equal((await call(claimRequest("a", "todo_rotation"))).status, "rejected");
    const historic = {...claimRequest("a", "todo_disconnect"), store_identity: next};
    assert.equal((await call(historic, "recover")).historical_only, true);
    assert.equal((await call({...claimRequest("a", "todo_rotation", "unknown-operation"), store_identity: next}, "recover")).status, "missing");
    assert.equal(result(await call({...claimRequest("a", "todo_rotation"), store_identity: next})).status, "applied");
  });
  await t.test("TLS verification failures never trigger a retry or expose a credential", async () => {
    const before = await fixture.store.loadAuthority();
    const failed = await callRemoteAuthority(running.endpoint, credentials.a, claimRequest("a"), {timeoutMs: 1000});
    assert.equal(failed.status, "ambiguous");
    assert(!JSON.stringify(failed).includes(credentials.a));
    assert.deepEqual(await fixture.store.loadAuthority(), before);
  });
  await t.test("negative receipt and identity share one PostgreSQL snapshot across mid-read rotation", async () => {
    const before = await fixture.store.storeIdentity(); assert.equal(before.status, "available");
    if (before.status !== "available") return;
    const next = `postgresql:${"e".repeat(32)}`;
    let rotated = false;
    const delayed: PostgreSqlAuthorityDatabase = {connect: async () => {
      const connection = await fixture.runtimeDatabase.connect();
      return {...connection, query: async (sql, args) => {
        const result = await connection.query(sql, args);
        if (!rotated && sql.includes("authority_store_metadata")) {
          rotated = true;
          assert.equal((await rotatePostgreSqlAuthorityStoreIdentity(fixture.database, before.store_identity, next)).status, "rotated");
        }
        return result;
      }};
    }};
    const store = new PostgreSqlAuthorityStore(delayed, {tenant_id: "tenant-one", goal_id: "goal-one"});
    assert.deepEqual(await store.readReceiptSnapshot("unknown-operation"), {
      status: "snapshot", store_identity: before.store_identity, receipt: {status: "missing"},
    });
    assert.deepEqual(await fixture.store.storeIdentity(), {status: "available", store_identity: next});
    assert.deepEqual(await store.readReceiptSnapshot("unknown-operation"), {
      status: "snapshot", store_identity: next, receipt: {status: "missing"},
    });
    assert.deepEqual(await store.readReceipt("unknown-operation"), {status: "missing"});
  });
});

test("disposable database cleanup waits for client disconnects", {skip: !connectionString}, async t => {
  // Widen the same Pool.end()/socket-close race seen in CI, using real PG
  // connections. An early DROP DATABASE FORCE would emit an uncaught error.
  const end = Client.prototype.end;
  const delayed = t.mock.method(Client.prototype, "end", function(this: Client, ...args: Parameters<Client["end"]>) {
    setTimeout(() => end.apply(this, args), 100);
  });
  await t.test("owns an idle connection until fixture teardown", async child => {
    const fixture = await postgresRemoteFixture(child, connectionString!);
    await fixture.seed(["todo_cleanup"]);
  });
  assert(delayed.mock.callCount() > 0);
});
