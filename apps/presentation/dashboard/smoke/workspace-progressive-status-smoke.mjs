// Exercise the production loader, including streamed error bodies and cancellation.
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "vite";

const outDir = resolve("node_modules/.cache/workspace-progressive");
await build({ configFile: false, logLevel: "silent", build: {
  outDir, emptyOutDir: true,
  lib: { entry: resolve("src/data/workspace-progressive-status.ts"), formats: ["es"], fileName: () => "loader.mjs" },
  rolldownOptions: { external: ["zod"] },
} });
const { loadWorkspaceGoalSnapshots, directoryStatusPayload, reusableGoalSnapshots, workspaceReadPlan } = await import(pathToFileURL(resolve(outDir, "loader.mjs")).href);
const directory = { ok: true, schema_version: "loopx_workspace_directory_v1", registry_revision: "r1",
  goals: [{ id: "alpha", display_name: "Alpha", activation_state: "active", registry_member: true }] };

// What a same-source refresh may keep: a paused Goal must not send its peers
// back to the loading lane, and the touched Goal must not survive as a stale
// snapshot.
const entry = (id, activation_state = "active", display_name = id) =>
  ({ id, display_name, activation_state, registry_member: true });
const snapshots = { alpha: { alpha: true }, beta: { beta: true }, gamma: { gamma: true } };
const earlier = {
  directory: { ok: true, schema_version: "loopx_workspace_directory_v1", registry_revision: "r1",
    goals: [entry("alpha"), entry("beta"), entry("gamma")] },
  snapshots,
};
const paused = { ok: true, schema_version: "loopx_workspace_directory_v1", registry_revision: "r2",
  goals: [entry("alpha"), entry("beta", "stopped"), entry("gamma")] };
assert.deepEqual(Object.keys(reusableGoalSnapshots(earlier, paused)), ["alpha", "gamma"],
  "a Goal whose lifecycle moved is re-read instead of reused");
assert.deepEqual(Object.keys(reusableGoalSnapshots(earlier, paused, { invalidateGoalIds: ["gamma"] })), ["alpha"],
  "a Goal the action touched is re-read even when its directory entry held");
assert.deepEqual(Object.keys(reusableGoalSnapshots(earlier, paused, { invalidateGoalIds: ["beta"] })), ["alpha", "gamma"],
  "invalidating the Goal that already changed keeps its peers");
assert.deepEqual(Object.keys(reusableGoalSnapshots(earlier, {
  ...paused, goals: [entry("alpha"), entry("gamma"), entry("delta")],
})), ["alpha", "gamma"], "a Goal that left the directory loses its snapshot");
assert.deepEqual(Object.keys(reusableGoalSnapshots(earlier, {
  ...paused, goals: [entry("alpha"), entry("beta", "active", "Renamed"), entry("gamma")],
})), ["alpha", "gamma"], "a renamed Goal is re-read rather than reused under its new title");
assert.deepEqual(reusableGoalSnapshots(null, paused), {}, "a first read has nothing to reuse");
assert.deepEqual(Object.keys(reusableGoalSnapshots(earlier, paused, { invalidateGoalIds: ["alpha", "gamma"] })), [],
  "invalidating every Goal is a full re-read");
const fullRefresh = workspaceReadPlan(earlier, earlier.directory);
assert.deepEqual(fullRefresh.snapshots, snapshots, "a full refresh keeps loaded content visible while reading");
assert.deepEqual(fullRefresh.requestedDirectory.goals.map((goal) => goal.id), ["alpha", "beta", "gamma"],
  "a full refresh reads peers whose lifecycle held, so external Todo writes become visible");
const partial = workspaceReadPlan(earlier, paused, "missing", { invalidateGoalIds: ["gamma"] });
assert.deepEqual(Object.keys(partial.snapshots), ["alpha"], "partial recovery keeps unaffected peers visible");
assert.deepEqual(partial.requestedDirectory.goals.map((goal) => goal.id), ["beta", "gamma"],
  "partial recovery reads moved and explicitly touched Goals");
assert.deepEqual(workspaceReadPlan(null, earlier.directory, "missing").snapshots, {},
  "a different source cannot retain the previous source's data");
const retentionChecks = 12;
const access = { error_code: "workspace_status_access_denied" };
const original = { fetch, setTimeout, clearTimeout };
const deadline = {};
let expireRequest;
let checks = 0;
globalThis.setTimeout = (callback, delay, ...args) => {
  if (delay === 30_000) { expireRequest = callback; return deadline; }
  return original.setTimeout(callback, 0, ...args);
};
globalThis.clearTimeout = (timer) => { if (timer !== deadline) original.clearTimeout(timer); };
async function check(name, respond, expected, attempts, verify = () => {}) {
  let calls = 0;
  let current = true;
  const controller = new AbortController();
  const results = [];
  globalThis.fetch = async () => {
    calls++;
    return respond({ expire: () => expireRequest(), cancel: () => controller.abort(), invalidate: () => { current = false; } });
  };
  let watchdog;
  try {
    await Promise.race([
      loadWorkspaceGoalSnapshots("http://status/status.json", "http://status", directory,
        (_id, _payload, error) => results.push(error ?? "success"), () => current, () => "alpha", controller.signal),
      new Promise((_resolve, reject) => { watchdog = original.setTimeout(() => reject(new Error(name + ": hung")), 2_000); }),
    ]);
    assert.deepEqual(results, expected, name);
    assert.equal(calls, attempts, name + ": attempts");
    verify();
    checks++;
  } finally { original.clearTimeout(watchdog); }
}
const json = (body, status = 500) => Response.json(body, { status });
try {
  await check("access", () => json(access), ["access"], 1);
  for (const body of [null, {}, [], { error_code: "future_code" }, { error_code: 5 }, { error: "permission denied" }]) {
    await check("unknown envelope", () => json(body), ["service"], 3);
  }
  await check("proxy HTML", () => new Response("<html>bad gateway</html>", { status: 502 }), ["service"], 3);
  await check("empty body", () => new Response(null, { status: 503 }), ["service"], 3);
  for (const status of [400, 401, 403, 404, 409]) {
    await check("HTTP precedence " + status, () => json(access, status), [status === 409 ? "revision" : "scope"], 1);
  }
  let cancelled = 0;
  await check("oversized stream ignores false content-length", () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(16 * 1024 + 1)); },
    cancel() { cancelled++; },
  }), { status: 500, headers: { "Content-Length": "1" } }), ["service"], 3, () => assert.equal(cancelled, 3));
  const encoded = new TextEncoder().encode(JSON.stringify(access));
  await check("fragmented JSON", () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(encoded.subarray(0, 5)); controller.enqueue(encoded.subarray(5)); controller.close(); },
  }), { status: 500 }), ["access"], 1);
  await check("broken error body", () => new Response(new ReadableStream({
    start(controller) { controller.error(new TypeError("broken stream")); },
  }), { status: 500 }), ["service"], 3);
  for (const action of ["expire", "cancel", "invalidate"]) {
    await check("incomplete body " + action, (actions) => new Response(new ReadableStream({
      start(controller) { original.setTimeout(() => { actions[action](); if (action === "invalidate") controller.close(); }, 0); },
    }), { status: 500 }), action === "expire" ? ["timeout"] : [], action === "expire" ? 3 : 1);
  }
  await check("network", () => { throw new TypeError("fetch failed"); }, ["network"], 3);
  await check("invalid success", () => json({ workspace_registry_revision: "r1" }, 200), ["invalid"], 1);
  await check("revision", () => json({ workspace_registry_revision: "r2" }, 200), ["revision"], 1);
  const payload = { ...directoryStatusPayload(directory), workspace_registry_revision: "r1" };
  await check("success", () => json(payload, 200), ["success"], 1);
  await check("wrong Goal", () => json({ ...payload, run_history: { ...payload.run_history, goals: [{ id: "beta" }] } }, 200), ["scope"], 1);
  console.log(JSON.stringify({ ok: true, checks: checks + retentionChecks }));
} finally {
  globalThis.fetch = original.fetch;
  globalThis.setTimeout = original.setTimeout;
  globalThis.clearTimeout = original.clearTimeout;
}
