import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {ChatApiError, fetchAppStewardTarget, fetchPrivateConversations, selectAppSteward,
  fetchChatHistory, resolveBoundChatSession} from "../src/data/chat";

const repoRoot = resolve(process.cwd(), "../../..");
const child = spawn(resolveTestPython({repoRoot}), ["-u", "apps/presentation/dashboard/smoke/bound-conversation-http-fixture.py", "--steward"],
  {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
const exited = once(child, "exit");
const output = createInterface({input: child.stdout});
const lines = output[Symbol.asyncIterator]();
let stderr = "";
child.stderr.on("data", value => {stderr += String(value);});
async function next(): Promise<any> {
  for (;;) {
    let timer: ReturnType<typeof setTimeout>;
    const line = await Promise.race([lines.next(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Fixture timed out: ${stderr}`)), 20_000);
    })]).finally(() => clearTimeout(timer));
    assert.equal(line.done, false, stderr);
    if (line.value!.startsWith("{")) return JSON.parse(line.value!);
  }
}
const originalFetch = globalThis.fetch;
try {
  const {origin, bindingId, sessionId} = await next();
  globalThis.fetch = (input, init) => originalFetch(new URL(String(input), origin), init);
  let connections = await fetchPrivateConversations();
  const inspect = async () => {child.stdin.write("inspect\n"); return next();};
  const openDefault = async (overrides = {}) => {
    const response = await fetch("/api/chat/sessions", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({context_kind: "manager", ...overrides})});
    return {status: response.status, body: await response.json()};
  };
  const before = await inspect();
  assert.equal(before.sessions, 3);
  assert.equal(await fetchAppStewardTarget(), null, "No implicit selection between two original stewards");
  let peer = connections.connections.find(row => row.app_ref === "synthetic-peer")!;
  assert.ok(peer && peer.binding_id !== bindingId);
  connections = await selectAppSteward(bindingId, connections.revision);
  const target = {bindingId, sessionId};
  assert.deepEqual(await fetchAppStewardTarget(), target);
  const opened = await openDefault();
  assert.equal(opened.status, 200);
  assert.equal(opened.body.session_id, sessionId, "Default API entry must reuse the configured private conversation");
  for (const overrides of [{mode: "new"}, {agent_model_id: "different-model"}]) {
    assert.equal((await openDefault(overrides)).status, 400, "Explicit overrides cannot silently fork the selected source");
  }
  await assert.rejects(() => selectAppSteward(peer.binding_id, connections.revision - 1), /selection could not/);
  assert.deepEqual(await fetchAppStewardTarget(), target, "Stale selection cannot retarget the App");
  const history = await fetchChatHistory({bound: target});
  assert.ok(history.messages.length === 2 && history.messages.every(row => !row.text.includes("Different private steward")));
  connections = await selectAppSteward(peer.binding_id, connections.revision);
  const peerTarget = await fetchAppStewardTarget();
  assert.equal(peerTarget?.bindingId, peer.binding_id);
  assert.notEqual(peerTarget?.sessionId, sessionId);
  assert.equal((await resolveBoundChatSession(target)).session_id, sessionId,
    "An already opened original Session stays fixed after the entry preference changes");
  connections = await selectAppSteward(bindingId, connections.revision);
  child.stdin.write("deny\n"); await next();
  await assert.rejects(() => fetchAppStewardTarget(), /selected original steward is unavailable/);
  await assert.rejects(() => selectAppSteward(bindingId, connections.revision), /selection could not/);
  assert.equal((await openDefault()).status, 400, "Failed identity verification must not fall back to a local manager");
  child.stdin.write("owner-login-missing\n"); await next();
  await assert.rejects(() => fetchAppStewardTarget(), error => error instanceof ChatApiError
    && error.payload.error_code === "lark_owner_identity_unavailable");
  assert.equal((await openDefault()).status, 400);
  child.stdin.write("recover\n"); await next();
  assert.deepEqual(await fetchAppStewardTarget(), target);
  assert.equal((await openDefault()).body.session_id, sessionId, "Login recovery returns to the same original Session");
  const after = await inspect();
  assert.equal(after.sessions, before.sessions);
  assert.deepEqual(after.messages, before.messages);
  assert.equal(after.native_requests, before.native_requests, "Entry lookup and preference never invoke a model or replay a Turn");
  child.stdin.write("replace-peer\n");
  const replaced = await next();
  connections = await fetchPrivateConversations();
  const futurePeer = connections.connections.find(row => row.app_ref === "synthetic-peer")!;
  assert.notEqual(futurePeer.binding_id, peer.binding_id);
  assert.equal(futurePeer.binding_id, replaced.new_peer_binding_id);
  assert.deepEqual(await fetchAppStewardTarget(), target, "A newly connected steward cannot capture the selected entry");
  peer = futurePeer;
  const beforeClose = await inspect();
  child.stdin.write("hold-steward-discovery\n");
  assert.deepEqual(await next(), {steward_discovery_armed: true});
  const opening = openDefault();
  assert.deepEqual(await next(), {steward_discovery_held: true});
  try {
    const closed = await fetch(`/api/chat/sessions/${sessionId}`, {method: "DELETE"});
    assert.equal(closed.status, 200);
    assert.equal((await closed.json()).closed, true);
  } finally {
    child.stdin.write("resume-steward-discovery\n");
    assert.deepEqual(await next(), {steward_discovery_released: true});
  }
  const rejected = await opening;
  assert.equal(rejected.status, 400, JSON.stringify(rejected.body));
  assert.match(rejected.body.error, /original steward Session is unavailable/i);
  const afterClose = await inspect();
  assert.equal(afterClose.sessions, beforeClose.sessions);
  assert.deepEqual(afterClose.messages, beforeClose.messages);
  assert.equal(afterClose.native_requests, beforeClose.native_requests);
  await assert.rejects(() => fetchAppStewardTarget(), /selected original steward is unavailable/);
  child.stdin.write("revoke\n"); await next();
  connections = await fetchPrivateConversations();
  assert.equal(connections.app_steward_binding_id, bindingId);
  await assert.rejects(() => fetchAppStewardTarget(), /selected original steward is unavailable/);
  assert.equal((await fetchPrivateConversations()).connections[0].binding_id, peer.binding_id);
  connections = await selectAppSteward(null, connections.revision);
  assert.equal(await fetchAppStewardTarget(), null, "Only an explicit clear restores the ordinary entry");
  const local = await openDefault();
  assert.equal(local.status, 201);
  assert.equal(local.body.session.channel_id, "manager", "Cleared selection preserves the existing local manager path");
  const routedFetch = globalThis.fetch;
  for (const malformed of [{ok: true}, {ok: true, binding_id: null, session_id: sessionId},
    {ok: true, binding_id: bindingId, session_id: "../other"}]) {
    globalThis.fetch = async () => new Response(JSON.stringify(malformed));
    await assert.rejects(() => fetchAppStewardTarget());
  }
  globalThis.fetch = routedFetch;
  child.stdin.write("stop\n");
  const [code] = await exited;
  assert.equal(code, 0, stderr);
  console.log("app-steward: passed (real HTTP/Core, explicit choice between sources, original Session, stale/identity/revocation refusal, no replay or fallback)");
} finally {
  globalThis.fetch = originalFetch;
  output.close();
  if (child.exitCode === null) {child.kill("SIGINT"); await exited;}
}
