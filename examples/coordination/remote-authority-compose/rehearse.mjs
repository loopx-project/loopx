#!/usr/bin/env node
// Exercise the packaged entry points against a disposable Compose project.
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {randomBytes} from "node:crypto";
import {createServer} from "node:net";
import {resolve} from "node:path";
import {performance} from "node:perf_hooks";
import {setTimeout as sleep} from "node:timers/promises";

const [bundle] = process.argv.slice(2);
if (!bundle || process.argv.length !== 3 || bundle === "--help") {
  console.log("Usage: node rehearse.mjs <unpacked bundle directory>");
  process.exit(bundle === "--help" ? 0 : 2);
}

const project = `loopx-rehearsal-${randomBytes(6).toString("hex")}`;
const cwd = resolve(bundle);
const env = {...process.env, COMPOSE_PROJECT_NAME: project, LOOPX_PORT: await freeLoopbackPort(),
  LOOPX_READ_PORT: await freeLoopbackPort()};
const started = performance.now();
let readySeconds;
let operationError;

async function freeLoopbackPort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return String(port);
}

function run(command, args, {input, allowRejected = false} = {}) {
  const result = spawnSync(command, args, {cwd, env, input, encoding: "utf8", timeout: 150_000});
  if (result.error) throw result.error;
  if (result.signal || (result.status !== 0 && !allowRejected)) {
    throw new Error(`${command} ${args.join(" ")} exited ${result.status ?? result.signal}: ${result.stderr.slice(-2048)}`);
  }
  return result.stdout;
}

function client(actor, action, request) {
  return JSON.parse(run("./client.sh", [actor, action], {
    input: request === undefined ? undefined : JSON.stringify(request),
    allowRejected: true,
  }));
}

try {
  const startup = run("./start.sh", []);
  const a = client("a", "descriptor");
  const b = client("b", "descriptor");
  const reader = client("reader", "descriptor");
  const aStart = client("a", "read");
  const bStart = client("b", "read");
  assert.equal(aStart.status, "found");
  assert.equal(bStart.status, "found");
  assert.equal(aStart.provider_revision, bStart.provider_revision);
  assert.deepEqual(aStart.todo, bStart.todo);
  assert.notEqual(a.actor_id, b.actor_id);
  assert.equal(client("reader", "read").schema_version, "loopx_remote_authority_scoped_read_response_v1");
  readySeconds = Math.round((performance.now() - started) / 100) / 10;
  const wrongStore = {...b, store_identity: "postgresql:" + "0".repeat(32)};
  assert.equal(client("b", "request", wrongStore).status, "rejected");
  assert.equal(client("b", "request", {...b, actor_id: a.actor_id}).status, "rejected");
  const hidden = "todo_outside_read_grant";
  assert.equal(client("a", "request", {...a, todo_id: hidden, operation: "todo_add",
    request_id: "rehearsal-create-hidden", text: "Task outside the reader grant",
    note: "Private writer-only note"}).result.status, "applied");
  const beforeReadChecks = client("a", "read").provider_revision;
  assert.equal(client("reader", "request", {...reader, todo_id: hidden}).status, "rejected");
  assert.equal(client("reader", "request", {...reader, operation: "claim", request_id: "reader-cannot-claim"}).status, "rejected");
  for (const scope of [{tenant_id: "other"}, {goal_id: "other"}, {actor_id: a.actor_id},
    {store_identity: "postgresql:" + "0".repeat(32)}]) {
    assert.equal(client("reader", "request", {...reader, ...scope}).status, "rejected");
  }
  const {todo_id: ignored, ...readerScope} = reader;
  const snapshot = client("reader", "request", {...readerScope, operation: "snapshot"});
  assert.equal(snapshot.status, "snapshot");
  assert.deepEqual(snapshot.todos.map(row => row.todo_id), [reader.todo_id]);
  assert.equal(Object.hasOwn(snapshot, "provider_revision"), false);
  assert.equal(JSON.stringify(snapshot).includes("Private writer-only note"), false);
  assert.equal(client("a", "read").provider_revision, beforeReadChecks);

  const claimA = {...a, operation: "claim", request_id: "rehearsal-claim-a"};
  const acquired = client("a", "request", claimA).result;
  assert.equal(acquired.status, "applied");
  const progressA = {...a, operation: "progress", request_id: "rehearsal-progress-a",
    claim_request_id: claimA.request_id, note: "First client prepared the handoff"};
  assert.equal(client("a", "request", progressA).result.status, "applied");
  const readA = client("a", "read");
  const renewed = client("a", "request", {...a, operation: "renew", ttl_seconds: 20,
    idempotency_key: acquired.lease.idempotency_key, expected_version: readA.lease.version}).result;
  assert.equal(renewed.status, "applied");
  const beforeTakeover = client("b", "request", {...b, operation: "claim", request_id: "rehearsal-too-early"});
  assert.equal(beforeTakeover.result.status, "failed");
  const remaining = Date.parse(renewed.lease.expires_at) - Date.now();
  assert(Number.isFinite(remaining) && remaining <= 21_000);
  await sleep(Math.max(remaining, 0) + 250);

  const claimB = {...b, operation: "claim", request_id: "rehearsal-claim-b"};
  const takeover = client("b", "request", claimB).result;
  assert.equal(takeover.status, "applied");
  assert(takeover.lease.lease_epoch > renewed.lease.lease_epoch);
  assert.equal(client("a", "request", {...progressA, request_id: "rehearsal-stale-owner"}).result.status, "failed");
  const complete = {...b, operation: "complete", request_id: "rehearsal-complete",
    claim_request_id: claimB.request_id, evidence: "evidence:compose-rehearsal"};
  assert.equal(client("b", "request", complete).result.status, "applied");
  const recover = client("b", "recover", complete);
  assert.equal(recover.status, "found");
  assert.equal(recover.historical_only, true);
  assert.match(recover.receipt_sha256, /^[a-f0-9]{64}$/);

  run("docker", ["compose", "down", "--timeout", "5"]);
  run("./start.sh", []);
  assert.equal(client("b", "recover", complete).receipt_sha256, recover.receipt_sha256);
  assert.equal(client("b", "request", complete).result.status, "replayed");
  const aEnd = client("a", "read");
  const bEnd = client("b", "read");
  assert.equal(aEnd.todo.status, "done");
  assert.deepEqual(aEnd.todo, bEnd.todo);
  assert.equal(aEnd.provider_revision, bEnd.provider_revision);
  assert.equal(client("reader", "read").todo.status, "done");
  assert.equal(client("reader", "recover", {...complete, actor_id: reader.actor_id}).status, "rejected");
  function policyScript(code) {
    run("docker", ["compose", "run", "--rm", "--no-deps", "-T", "--entrypoint", "node", "secrets", "-e", code]);
  }
  const readerPolicy = "/secrets/reader-gateway/policy.json";
  policyScript(`const fs = require('node:fs'); const p = '${readerPolicy}';
    fs.renameSync(p, p + '.saved'); fs.writeFileSync(p + '.next', JSON.stringify({
      ...JSON.parse(fs.readFileSync(p + '.saved')), principals: []})); fs.renameSync(p + '.next', p);`);
  assert.equal(client("reader", "read").status, "rejected");
  run("docker", ["compose", "restart", "reader-gateway"]);
  const listeningDeadline = performance.now() + 15_000;
  let revoked = client("reader", "read");
  while (revoked.status === "unavailable" && performance.now() < listeningDeadline) {
    await sleep(200);
    revoked = client("reader", "read");
  }
  assert.equal(revoked.status, "rejected");
  assert.equal(revoked.reason_code, "principal_unauthenticated");
  policyScript(`const fs = require('node:fs'); const p = '${readerPolicy}'; fs.unlinkSync(p);`);
  assert.equal(client("reader", "read").status, "unavailable");
  policyScript(`const fs = require('node:fs'); const p = '${readerPolicy}'; fs.writeFileSync(p, '{');`);
  assert.equal(client("reader", "read").status, "unavailable");
  policyScript(`const fs = require('node:fs'); const p = '${readerPolicy}'; fs.renameSync(p + '.saved', p);`);
  assert.equal(client("reader", "read").todo.status, "done");
  assert.equal(client("a", "read").provider_revision, aEnd.provider_revision);
  const architecture = run("docker", ["compose", "exec", "-T", "gateway", "uname", "-m"]).trim();
  console.log(JSON.stringify({ok: true, architecture, twoClientReadySeconds: readySeconds,
    startup: startup.trim(), leaseTakeover: true, receiptPreserved: true,
    scopedReadDefaultDeny: true, scopedWriteUnavailable: true, revocationPreserved: true}));
} catch (error) {
  operationError = error;
  throw error;
} finally {
  try {
    run("docker", ["compose", "down", "--timeout", "5", "--volumes"]);
  } catch (error) {
    if (!operationError) throw error;
    console.error(`Cleanup of ${project} also failed: ${error.message}`);
  }
}
