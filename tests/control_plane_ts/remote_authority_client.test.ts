import assert from "node:assert/strict";
import test, {type TestContext} from "node:test";
import {createServer} from "node:https";
import type {AddressInfo} from "node:net";
import {execFile, spawn} from "node:child_process";
import {promisify} from "node:util";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  decodeRemoteAuthorityRequest,
  decodeRemoteAuthorityResponse,
  POSTGRESQL_STORE_IDENTITY_PATTERN,
  REMOTE_AUTHORITY_REQUEST,
  REMOTE_AUTHORITY_SCOPED_READ_RESPONSE,
  remoteAuthorityOperationId,
  remoteAuthorityResponse,
  remoteAuthoritySnapshotDigest,
  type RemoteAuthorityMode,
  type RemoteAuthorityRequest,
} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {callRemoteAuthority} from "../../loopx/control_plane/coordination/remote_authority_http.ts";

const storeIdentity = `postgresql:${"c".repeat(32)}`;
const credential = `fixture-${"a".repeat(48)}`;
const repoRoot = fileURLToPath(new URL("../../", import.meta.url));

function claimRequest(todo = "todo_one", id = `claim-${todo}`): Extract<RemoteAuthorityRequest, {operation: "claim"}> {
  return {
    schema_version: REMOTE_AUTHORITY_REQUEST,
    tenant_id: "tenant-one",
    goal_id: "goal-one",
    actor_id: "actor-a",
    store_identity: storeIdentity,
    todo_id: todo,
    operation: "claim",
    request_id: id,
  };
}

async function tlsFixture(t: TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-remote-client-tls-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const keyPath = join(root, "key.pem"), certPath = join(root, "cert.pem");
  await promisify(execFile)("openssl", [
    "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    "-keyout", keyPath, "-out", certPath,
  ]);
  return {root, keyPath, certPath, key: await readFile(keyPath), cert: await readFile(certPath)};
}

test("remote commands reject caller authority, time, raw state and unknown operations", () => {
  assert.equal(POSTGRESQL_STORE_IDENTITY_PATTERN.test(storeIdentity), true);
  assert.equal(POSTGRESQL_STORE_IDENTITY_PATTERN.test("file:invalid"), false);
  const request = claimRequest();
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

for (const operation of ["renew", "release"] as const) {
  test(`remote ${operation} versions use the canonical non-negative safe-integer range`, () => {
    const {request_id: _, ...scope} = claimRequest() as ReturnType<typeof claimRequest> & {request_id: string};
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
  const {request_id: _, ...scope} = claimRequest() as ReturnType<typeof claimRequest> & {request_id: string};
  const request = decodeRemoteAuthorityRequest({...scope, operation: "release", idempotency_key: "existing-key", expected_version: 0});
  const result: JsonObject = {
    status: "no_change", changed: false, released: false, missing: true,
    handoff_mode: "hard_lease", provider_revision: `${request.store_identity}:3`, cursor: "3",
  };
  const wrap = (payload: JsonObject) => remoteAuthorityResponse(request, "execute", {
    status: "result", operation_id: remoteAuthorityOperationId(request, "principal-a"), result: payload,
  });
  assert.deepEqual(decodeRemoteAuthorityResponse(wrap(result), request, "execute"), wrap(result));
  for (const field of ["missing", "released", "changed", "provider_revision", "cursor"]) {
    const incomplete = {...result}; delete incomplete[field];
    assert.throws(() => decodeRemoteAuthorityResponse(wrap(incomplete), request, "execute"));
  }
  for (const patch of [{released: true}, {changed: true}, {missing: false}, {lease: {}}, {status: "applied"}]) {
    assert.throws(() => decodeRemoteAuthorityResponse(wrap({...result, ...patch}), request, "execute"));
  }
});

test("scoped read and historical receipt responses enforce Goal binding and projection boundaries", () => {
  const {request_id: _, ...scope} = claimRequest();
  const goal_ref = {goal_id: scope.goal_id, goal_instance_id: "ginst_" + "a".repeat(32)};
  const todo = {todo_id: scope.todo_id, status: "open", claimed_by: "actor-a"};
  const lease = {owner: "actor-a", status: "active", expires_at: "2099-01-01T00:00:00Z", version: 1, lease_epoch: 1};
  const read = decodeRemoteAuthorityRequest({...scope, operation: "read"});
  const snapshot = decodeRemoteAuthorityRequest({...scope, operation: "snapshot"});
  const wrap = (request: typeof read, payload: JsonObject) => remoteAuthorityResponse(
    request, "execute", {...payload, goal_ref}, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE,
  );
  const found = (row: JsonObject, held: JsonObject) => wrap(read, {status: "found", todo: row, lease: held});
  const snap = (row: JsonObject, held: JsonObject) => wrap(snapshot, {
    status: "snapshot", goal_id: scope.goal_id, store_identity: scope.store_identity,
    todos: [row], leases: [held],
    receipt_sha256: remoteAuthoritySnapshotDigest(snapshot, undefined, [row], [held], goal_ref),
  });
  const snapshotTodo = {...todo, role: "agent", priority: "P1"};
  const snapshotLease = {...lease, goal_id: scope.goal_id, todo_id: scope.todo_id};
  assert.deepEqual(decodeRemoteAuthorityResponse(found(todo, lease), read, "execute"), found(todo, lease));
  assert.deepEqual(decodeRemoteAuthorityResponse(snap(snapshotTodo, snapshotLease), snapshot, "execute"), snap(snapshotTodo, snapshotLease));
  for (const field of ["note", "note_omitted", "text", "idempotency_key", "provider_revision", "cursor", "unknown"]) {
    const value = field === "note_omitted" ? true : "must-not-cross-the-read-boundary";
    assert.throws(() => decodeRemoteAuthorityResponse(found({...todo, [field]: value}, lease), read, "execute"));
    assert.throws(() => decodeRemoteAuthorityResponse(found(todo, {...lease, [field]: value}), read, "execute"));
    assert.throws(() => decodeRemoteAuthorityResponse(snap({...snapshotTodo, [field]: value}, snapshotLease), snapshot, "execute"));
    assert.throws(() => decodeRemoteAuthorityResponse(snap(snapshotTodo, {...snapshotLease, [field]: value}), snapshot, "execute"));
  }
  const claim = claimRequest();
  const historical = {
    status: "found", historical_only: true, goal_ref, todo_id: claim.todo_id,
    operation_id: remoteAuthorityOperationId(claim, "principal-a"), receipt_sha256: "d".repeat(64),
  };
  const wrapRecover = (value: JsonObject) => remoteAuthorityResponse(claim, "recover", value, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE);
  assert.deepEqual(decodeRemoteAuthorityResponse(wrapRecover(historical), claim, "recover"), wrapRecover(historical));
  for (const invalid of [
    {...historical, todo_id: "todo_other"},
    {...historical, historical_only: false},
    {...historical, result: {lease: {idempotency_key: "out-of-scope"}}},
    {...historical, cursor: "2"},
  ]) assert.throws(() => decodeRemoteAuthorityResponse(wrapRecover(invalid), claim, "recover"));
});

test("complete, block, defer, todo_add and snapshot request/response decoding validates lifecycle semantics", () => {
  const base = {
    schema_version: REMOTE_AUTHORITY_REQUEST,
    tenant_id: "tenant-one",
    goal_id: "goal-one",
    actor_id: "actor-a",
    store_identity: storeIdentity,
    todo_id: "todo_lifecycle_fixture",
  };
  const completeReq = decodeRemoteAuthorityRequest({
    ...base, operation: "complete", request_id: "req-complete-1",
    claim_request_id: "req-claim-1", evidence: "sha256:abcd verified delivery receipt", note: "optional completion note",
  });
  assert.throws(() => decodeRemoteAuthorityRequest({...completeReq, evidence: ""}));

  const blockReq = decodeRemoteAuthorityRequest({
    ...base, operation: "block", request_id: "req-block-1",
    reason: "waiting on upstream schema", resume_when: "todo_done:todo_schema_fixture", note: "paused temporarily",
  });
  assert.throws(() => decodeRemoteAuthorityRequest({...blockReq, resume_when: "schema_applied"}));

  const deferReq = decodeRemoteAuthorityRequest({
    ...base, operation: "defer", request_id: "req-defer-1",
    reason: "resuming next week", resume_when: "resume_at:2026-11-20T00:00:00Z",
  });
  assert.throws(() => decodeRemoteAuthorityRequest({...deferReq, resume_when: ""}));

  const addReq = decodeRemoteAuthorityRequest({
    ...base, operation: "todo_add", request_id: "req-add-1",
    text: "[P1] new remote task", priority: "P1", role: "agent",
    task_class: "advancement_task", required_write_scopes: ["loopx/control_plane/**"],
  });
  assert.throws(() => decodeRemoteAuthorityRequest({...addReq, priority: "P9"}));

  const snapshotReq = decodeRemoteAuthorityRequest({
    schema_version: REMOTE_AUTHORITY_REQUEST,
    tenant_id: "tenant-one", goal_id: "goal-one", actor_id: "actor-a",
    store_identity: storeIdentity, operation: "snapshot", todo_id: "todo_lifecycle_fixture",
  });
  const todo = {todo_id: "todo_lifecycle_fixture", status: "open", role: "agent"};
  const facts = {provider_revision: `${base.store_identity}:10`, store_identity: base.store_identity,
    goal_id: base.goal_id, todos: [todo], leases: []};
  const validSnap = remoteAuthorityResponse(snapshotReq, "execute", {
    status: "snapshot", ...facts,
    receipt_sha256: canonicalAuthoritySha256({schema_version: "loopx_goal_snapshot_receipt_v0", tenant_id: base.tenant_id, ...facts}),
  });
  assert.deepEqual(decodeRemoteAuthorityResponse(validSnap, snapshotReq, "execute"), validSnap);

  const opId = remoteAuthorityOperationId(completeReq, "principal-a");
  const compResponse = remoteAuthorityResponse(completeReq, "execute", {
    status: "result", operation_id: opId,
    result: {
      status: "applied", changed: true, completed_at: "2026-12-01T12:00:00Z",
      completion_receipt_id: "receipt-1", todo_id: base.todo_id,
      provider_revision: `${base.store_identity}:11`, cursor: "11",
    },
  });
  assert.deepEqual(decodeRemoteAuthorityResponse(compResponse, completeReq, "execute"), compResponse);
});

test("callRemoteAuthority, CLI client and probe verify TLS and classify transport uncertainty", async t => {
  for (const endpoint of ["http://localhost/", "https://user:secret@localhost/", "https://localhost/?credential=x", "https://localhost/path"]) {
    await assert.rejects(callRemoteAuthority(endpoint, credential, claimRequest()));
  }
  await assert.rejects(callRemoteAuthority("https://localhost/", "short", claimRequest()));

  const tls = await tlsFixture(t);
  let statusCode = 200;
  let customBody: string | null = null;
  const server = createServer({key: tls.key, cert: tls.cert, minVersion: "TLSv1.2"}, async (req, res) => {
    res.setHeader("Content-Type", "application/json");
    if (req.headers.authorization !== `Bearer ${credential}`) {
      res.writeHead(401).end(JSON.stringify({status: "rejected", reason_code: "unauthorized"}));
      return;
    }
    const mode: RemoteAuthorityMode = req.url === "/v0/tasks/recover" ? "recover" : "execute";
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    const parsed = decodeRemoteAuthorityRequest(JSON.parse(Buffer.concat(chunks).toString("utf8")));
    if (customBody !== null || statusCode !== 200) {
      res.writeHead(statusCode).end(customBody ?? "{}");
      return;
    }
    if (parsed.operation === "read") {
      res.writeHead(200).end(JSON.stringify(remoteAuthorityResponse(parsed, mode, {
        status: "found", provider_revision: `${parsed.store_identity}:1`,
        todo: {todo_id: parsed.todo_id, status: "open"}, lease: null,
      })));
      return;
    }
    res.writeHead(200).end(JSON.stringify(remoteAuthorityResponse(parsed, mode, {
      status: "found", historical_only: true, operation_id: remoteAuthorityOperationId(parsed, "principal-a"),
      receipt_sha256: "b".repeat(64), provider_revision: `${parsed.store_identity}:2`, cursor: "2",
      result: {
        changed: true, todo_id: parsed.todo_id, claimed_by: parsed.actor_id,
        claim_operation_id: remoteAuthorityOperationId(parsed, "principal-a"), handoff_mode: "hard_lease",
        lease: {
          goal_id: parsed.goal_id, todo_id: parsed.todo_id, owner: parsed.actor_id,
          idempotency_key: "lease-1", status: "active", expires_at: "2099-01-01T00:00:00Z", version: 1, lease_epoch: 1,
        },
      },
    })));
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  t.after(() => new Promise<void>(resolve => server.close(() => resolve())));
  const endpoint = `https://127.0.0.1:${(server.address() as AddressInfo).port}`;

  // Untrusted self-signed cert without CA fails closed to transport_uncertain
  const untrusted = await callRemoteAuthority(endpoint, credential, claimRequest());
  assert.equal(untrusted.status, "ambiguous");
  assert.equal(untrusted.reason_code, "remote_authority_transport_uncertain");

  // Verified CA succeeds for read & recover
  const readReq = decodeRemoteAuthorityRequest({
    schema_version: REMOTE_AUTHORITY_REQUEST, tenant_id: "tenant-one", goal_id: "goal-one",
    actor_id: "actor-a", store_identity: storeIdentity, todo_id: "todo_probe", operation: "read",
  });
  const readRes = await callRemoteAuthority(endpoint, credential, readReq, {ca: tls.cert});
  assert.equal(readRes.status, "found");

  // Non-200 and oversized payloads map mutations to ambiguous and reads/recoveries to unavailable
  statusCode = 503;
  assert.equal((await callRemoteAuthority(endpoint, credential, readReq, {ca: tls.cert})).status, "unavailable");
  assert.equal((await callRemoteAuthority(endpoint, credential, claimRequest(), {ca: tls.cert})).status, "ambiguous");
  statusCode = 200;
  customBody = "x".repeat(70 * 1024);
  assert.equal((await callRemoteAuthority(endpoint, credential, claimRequest(), {ca: tls.cert, mode: "recover"})).status, "unavailable");
  customBody = null;

  // Subprocess smoke for examples/coordination/remote-authority-client.ts and remote_authority_probe.ts
  const runChild = (script: string, args: string[], stdinText: string, envExtra: Record<string, string> = {}) =>
    new Promise<{code: number | null; stdout: string}>((resolve, reject) => {
      const child = spawn(process.execPath, ["--no-warnings", "--experimental-strip-types", script, ...args], {
        cwd: repoRoot, env: {PATH: process.env.PATH ?? "", ...envExtra},
      });
      let stdout = "";
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", chunk => {stdout += chunk;});
      child.on("error", reject);
      child.on("close", code => resolve({code, stdout}));
      child.stdin.end(stdinText);
    });

  const cliResult = await runChild(
    "examples/coordination/remote-authority-client.ts",
    ["--endpoint", endpoint, "--ca", tls.certPath, "--recover"],
    JSON.stringify(claimRequest()),
    {LOOPX_AUTHORITY_CREDENTIAL: credential},
  );
  assert.equal(cliResult.code, 0);
  assert.equal(JSON.parse(cliResult.stdout).status, "found");

  const probeResult = await runChild(
    "loopx/control_plane/coordination/remote_authority_probe.ts",
    [],
    JSON.stringify({
      endpoint, credential, ca_file: tls.certPath,
      tenant_id: "tenant-one", goal_id: "goal-one", actor_id: "actor-a",
      store_identity: storeIdentity, probe_todo_id: "todo_probe",
    }),
  );
  assert.equal(probeResult.code, 0);
  assert.equal(probeResult.stdout, "verified\n");
});
