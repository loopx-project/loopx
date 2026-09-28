import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {connect} from "node:net";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";
import {FileAuthorityJournal} from "../../loopx/control_plane/coordination/file_authority_journal.ts";
import {FileAuthorityStore, fileAuthorityRevision} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

// A private child process and explicitly rooted store avoid touching the user's
// shared runtime. No tempfile cache, production locator or installed CLI is used.
test("a real runtime serves lightweight work during cold File history verification", {timeout: 60_000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-runtime-fairness-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const goal = "fairness-fixture";
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), goal);
  const identity = await store.storeIdentity();
  assert.equal(identity.status, "available");
  if (identity.status !== "available") throw new Error("fixture identity unavailable");
  const revision = (previous: string | null, transaction: Parameters<typeof fileAuthorityRevision>[3]) =>
    fileAuthorityRevision(goal, identity.store_identity, previous, transaction);
  const projection = productionScaleCoordinationFixture(goal).projection;
  let journal: FileAuthorityJournal | null = null;
  // Retain the actual mixed Todo/lease/decision shapes. Repeated observations
  // have distinct receipts and revisions even when they publish unchanged state.
  for (let i = 0; i < 96; i++) {
    journal = FileAuthorityJournal.append(journal, goal, identity.store_identity, {
      expected_provider_revision: journal?.provider_revision ?? null,
      operation_id: `observation-${i}`, events: [{kind: "observed", sequence: i}],
      next_projection: projection, receipts: [{sequence: i}],
    }, revision);
  }
  await writeFile(store.path, canonicalAuthorityBytes(journal!.toDocument()));
  const infoPath = join(root, "runtime.json");
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    "loopx/control_plane/effect_runtime_server.ts", "--info", infoPath, "--fingerprint", "fairness-fixture"], {
    env: {...process.env, LOOPX_EFFECT_RUNTIME_TOKEN: "isolated-test-token"}, stdio: "ignore",
  });
  const closed = once(child, "close");
  t.after(async () => { if (child.exitCode === null) child.kill(); await closed; });
  let info: {port: number; token: string} | undefined;
  for (let i = 0; i < 200; i++) {
    try { info = JSON.parse(await readFile(infoPath, "utf8")); break; }
    catch { if (child.exitCode !== null) throw new Error("isolated runtime exited"); await delay(20); }
  }
  assert.ok(info, "isolated runtime published its locator");
  const port = info.port, token = info.token;
  let sequence = 0;
  function rpc(method: string, params: Record<string, unknown> = {}): Promise<any> {
    return new Promise((resolve, reject) => {
      const socket = connect(port, "127.0.0.1");
      socket.setEncoding("utf8");
      socket.setTimeout(10_000, () => socket.destroy(new Error("original 10s response budget exceeded")));
      socket.on("error", reject);
      socket.on("connect", () => socket.write(JSON.stringify({schema_version: "loopx_effect_runtime_request_v0",
        token, request_id: `request-${sequence++}`, method, params}) + "\n"));
      let raw = "";
      socket.on("data", chunk => { raw += chunk; });
      socket.on("end", () => {
        try { const result = JSON.parse(raw); assert.equal(result.ok, true); resolve(result.result); }
        catch (error) { reject(error); }
      });
    });
  }
  let heavyFinished = false;
  const heavy = rpc("coordination.local_authority.operation_receipt", {
    schema_version: "loopx_local_coordination_operation_receipt_request_v0", runtime_root: root,
    goal_id: goal, operation_id: "observation-0",
  }).finally(() => { heavyFinished = true; });
  void heavy.catch(() => {});
  // Give the dedicated server time to enter its cold read, then exercise a
  // separate socket while its historical proof is still in flight.
  await delay(100);
  const binding = {id: "review", agent_id: "reviewer", todo_id: "todo_review", workspace: root,
    requesters: ["coordinator"], host_args: ["--host", "dsh"], timeout_seconds: 60, output_refs: ["output.json"]};
  const [ping, decisions, scope, ownership, selectedBinding] = await Promise.all([rpc("runtime.ping"), rpc("todo.standing_decision.project", {
    schema_version: "standing_decision_projection_request_v0", items: [], legacy_source_order: false,
  }), rpc("todo.authoring_scope.plan", {
    schema_version: "todo_authoring_scope_request_v0", command: "class", role: "agent",
    todo: {task_class: "continuous_monitor"}, intent: {},
  }), rpc("todo.ownership_gate.decide", {
    handoff_mode: "hard_lease", ownership_mutation: true, authority_mode: "registered_peer_actor",
  }), rpc("collaboration.delegation.binding", {
    agent_id: "coordinator", binding_id: "review",
    config: {schema_version: "loopx_local_delegation_v0", bindings: [binding]},
  })]);
  assert.deepEqual(selectedBinding, binding, "grant selection progresses without launching a Host");
  assert.equal(scope.schema_version, "todo_authoring_scope_result_v0");
  assert.equal(ownership.ownership_gate, "require_holder");
  assert.ok(ping.pid);
  assert.equal(decisions, null, "no standing decisions means no authority projection");
  assert.equal(heavyFinished, false, "a full history proof must not monopolize other requests");
  const receipt = await heavy;
  assert.equal(receipt.status, "found");
  assert.deepEqual(receipt.receipts, [{sequence: 0}]);
  assert.equal(receipt.cursor, "1");
  // A valid early receipt is not publishable if a later historical row fails.
  const corrupt = journal!.toDocument();
  const rows = corrupt.committed as Record<string, unknown>[];
  rows.at(-1)!.provider_revision = "tampered-last-row";
  const damagedBytes = canonicalAuthorityBytes(corrupt);
  await writeFile(store.path, damagedBytes);
  const refused = await rpc("coordination.local_authority.operation_receipt", {
    schema_version: "loopx_local_coordination_operation_receipt_request_v0", runtime_root: root,
    goal_id: goal, operation_id: "observation-0",
  });
  assert.equal(refused.status, "failed");
  assert.equal(refused.reason_code, "provider_protocol_violation");
  assert.deepEqual(await readFile(store.path), damagedBytes, "read failure must not rewrite history");
  await rpc("runtime.shutdown");
  await closed;
});
