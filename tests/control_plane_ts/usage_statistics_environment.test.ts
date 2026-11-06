import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, symlink } from "node:fs/promises";
import { platform, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { configure, inspect, observe } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import type { Context } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import { temporaryUsageEnvironment } from "../../loopx/control_plane/runtime/usage_statistics_environment.ts";

test("temporary machine routes suppress every HTTP channel even with enabled/copied state", async t => {
  const requests: Array<{ path: string; schema: string }> = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    requests.push({ path: request.url!, schema: JSON.parse(raw).schema });
    response.writeHead(204).end();
  });
  await new Promise<void>(ready => server.listen(0, "127.0.0.1", ready));
  t.after(() => new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done())));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  const root = await mkdtemp(join(tmpdir(), "loopx-usage-isolation-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "home", ".loopx", "usage-ping.json");
  const ctx: Context = { env: { HOME: join(root, "home"), LOOPX_USAGE_PING: "1",
    LOOPX_USAGE_PING_ENDPOINT: `http://127.0.0.1:${address.port}/v1/ping` },
    version: "1.2.3", python: "3.11", channel: "source", now: new Date("2026-11-06T12:00:00Z") };
  const automatic = { ...ctx, env: { ...ctx.env, LOOPX_USAGE_PING: undefined } };
  const initial = await inspect(path, automatic);
  assert.equal(initial.blocked_by, "temporary_path");
  assert.equal(initial.automatic_notice_required, false);
  await configure(path, automatic, "acknowledge", initial.notice);
  await assert.rejects(readFile(path), { code: "ENOENT" }, "automatic disclosure must not create a throwaway ID");

  await configure(path, ctx, "enable");
  const before = await readFile(path, "utf8");
  const { generation } = JSON.parse(before);
  const row = { feature: "todo", outcome: "ok", error: "none", duration: "lt_1s", count: 1 } as const;
  const end = ctx.now!.getTime() - 86400000; // Closed-day Goal summaries are eligible immediately.
  const goal = { key: "a".repeat(64), start: end - 60000, end, measurement: "host_call", host: "unknown" } as const;
  const diagnostic = { ...row, operation: "default", signal: "none", version: ctx.version, activity_day: "2026-11-06", context: "unknown" } as const;
  await observe(path, automatic, generation, row, undefined, goal, undefined, diagnostic);
  await observe(path, automatic, generation, null, undefined, undefined,
    { key: "b".repeat(64), lane: "c".repeat(64), turn: null, phase: "start", at: ctx.now!.getTime(), host: "unknown" });
  assert.deepEqual(requests, [], "heartbeat, CLI, Goal and installation must all stay silent");
  assert.equal(await readFile(path, "utf8"), before, "suppression cannot consume claims or pending counts");
  for (const suffix of [".goals", ".cycles", ".installation"]) await assert.rejects(readFile(path + suffix), { code: "ENOENT" });
  const remote = { ...ctx, env: { ...ctx.env, LOOPX_USAGE_PING_ENDPOINT: "https://collector.invalid/v1/ping" } };
  assert.equal((await inspect(path, remote)).blocked_by, "temporary_path", "explicit enable is not a remote bypass");

  await observe(path, ctx, generation, row, undefined, goal, undefined, diagnostic);
  assert.deepEqual(new Set(requests.map(r => r.path)), new Set(["/v1/ping", "/v1/aggregate", "/v1/goals", "/v1/installation"]));
  assert.equal(requests.length, 5, "positive control exercises both aggregate contracts as well as the other channels");
});

test("persistent machine routes retain notice-before-default-on without an enable flag", async t => {
  await mkdir(resolve(".local"), { recursive: true });
  const root = await mkdtemp(join(resolve(".local"), "usage-persistent-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "usage-ping.json");
  const ctx: Context = { env: { HOME: root, LOOPX_USAGE_PING_ENDPOINT: "http://127.0.0.1:1/v1/ping" },
    version: "1.2.3", python: "3.11", channel: "source" };
  const initial = await inspect(path, ctx);
  assert.equal(initial.blocked_by, "notice_required");
  assert.equal(initial.automatic_notice_required, true);
  await configure(path, ctx, "acknowledge", initial.notice);
  const state = JSON.parse(await readFile(path, "utf8"));
  assert.equal(state.consent, "default");
  assert.equal((await inspect(path, ctx)).sending, true);
  const sent: string[] = [];
  await observe(path, ctx, state.generation,
    { feature: "todo", outcome: "ok", error: "none", duration: "lt_1s", count: 1 },
    async (_url, payload) => { sent.push(payload.schema); return 204; });
  assert.ok(sent.includes("loopx_usage_ping_v1"));
  assert.ok(sent.includes("loopx_installation_usage_v1"));
});

test("temporary roots, filesystem aliases and path-component boundaries are local eligibility facts", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-usage-paths-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const persistent = resolve(".local", "usage-policy-persistent");
  for (const key of ["HOME", "USERPROFILE", "CODEX_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"]) {
    assert.equal(temporaryUsageEnvironment(join(persistent, "usage-ping.json"), { HOME: persistent, [key]: join(root, "home") }), true, key);
  }
  const configured = join(persistent, "scratch");
  for (const key of ["TMPDIR", "TEMP", "TMP"]) {
    assert.equal(temporaryUsageEnvironment(join(configured, "home", "usage-ping.json"), { HOME: persistent, [key]: configured }), true);
    assert.equal(temporaryUsageEnvironment(join(configured + "-persistent", "usage-ping.json"), { HOME: persistent, [key]: configured }), false);
  }
  assert.equal(temporaryUsageEnvironment(join(persistent, "tests", "usage-ping.json"), { HOME: persistent }), false,
    "a test-like name or a normal process temp directory must not suppress a persistent installation");
  await mkdir(resolve(".local"), { recursive: true });
  const links = await mkdtemp(join(resolve(".local"), "usage-alias-"));
  t.after(() => rm(links, { recursive: true, force: true }));
  await mkdir(join(root, "physical"));
  const alias = join(links, "alias");
  await symlink(join(root, "physical"), alias, platform() === "win32" ? "junction" : "dir");
  assert.equal(temporaryUsageEnvironment(join(alias, "not-created", "usage-ping.json"), { HOME: persistent }), true);
  if (platform() !== "win32") {
    for (const prefix of ["/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp",
      ...(platform() === "darwin" ? ["/var/folders", "/private/var/folders"] : [])]) {
      assert.equal(temporaryUsageEnvironment(join(prefix, "synthetic", "usage-ping.json"), { HOME: persistent }), true, prefix);
    }
  }
});
