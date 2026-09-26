import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import test from "node:test";
import { configure, inspect, observe, endpoint } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import type { Context, Post } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import { validAggregate, validPing, AGGREGATE_SCHEMA, durationBucket } from "../../loopx/control_plane/runtime/usage_statistics_contract.ts";
import type { Counter } from "../../loopx/control_plane/runtime/usage_statistics_contract.ts";

const row: Counter = { feature: "todo", outcome: "ok", duration: "lt_1s", error: "none", count: 1 };
const context = (day = "2026-09-26"): Context => ({ env: { LOOPX_USAGE_PING_ENDPOINT: "http://127.0.0.1:8787/v1/ping" }, version: "1.2.0", python: "3.13", channel: "source", now: new Date(day + "T12:00:00Z") });
async function fixture(t: test.TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-usage-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "usage-ping.json");
  const state = async () => JSON.parse(await readFile(path, "utf8"));
  return { path, state };
}
const noPost: Post = async () => { assert.fail("must not send"); };

test("fresh default requires notice; status is read-only; acknowledgment enables both channels", async t => {
  const { path, state } = await fixture(t); const ctx = context();
  const initial = await inspect(path, ctx);
  assert.equal(initial.consent, "default"); assert.equal(initial.blocked_by, "notice_required");
  await assert.rejects(readFile(path), /ENOENT/);
  await configure(path, ctx, "acknowledge", initial.notice);
  assert.equal((await inspect(path, ctx)).sending, true);
  assert.equal((await state()).consent, "default");
  assert.ok(validPing((await inspect(path, ctx)).next_payload));
});

test("old disabled remains disabled; old enabled preserves ID but requires new-scope disclosure", async t => {
  const { path } = await fixture(t); const ctx = context();
  for (const consent of ["disabled", "enabled"]) {
    await writeFile(path, JSON.stringify({ schema: "loopx_usage_ping_state_v0", consent, install_id: "00000000-0000-4000-8000-000000000001" }));
    const status = await inspect(path, ctx);
    assert.equal(status.sending, false);
    assert.equal(status.consent, consent);
    await configure(path, ctx, "acknowledge", status.notice);
    assert.equal((await inspect(path, ctx)).sending, consent === "enabled");
    if (consent === "enabled") assert.equal((await inspect(path, ctx)).next_payload?.install_id, "00000000-0000-4000-8000-000000000001");
  }
});

test("every suppressor overrides explicit enable and prevents writes and requests", async t => {
  const { path, state } = await fixture(t); const ctx = context();
  await configure(path, ctx, "enable"); const original = await readFile(path, "utf8");
  for (const env of [{ CI: "true" }, { DO_NOT_TRACK: "1" }, { LOOPX_USAGE_PING: "false" }, { LOOPX_USAGE_POLICY: "unknown" }]) {
    const blocked = { ...ctx, env: { ...ctx.env, ...env } };
    assert.equal((await inspect(path, blocked)).sending, false);
    await observe(path, blocked, (await state()).generation, row, noPost);
    assert.equal(await readFile(path, "utf8"), original);
  }
});

test("consent-required policy cannot be satisfied by acknowledgment; recipient changes invalidate notice", async t => {
  const { path } = await fixture(t); const ctx = context(); ctx.env.LOOPX_USAGE_POLICY = "consent_required";
  await configure(path, ctx, "acknowledge", (await inspect(path, ctx)).notice);
  assert.equal((await inspect(path, ctx)).blocked_by, "consent_required");
  await configure(path, ctx, "enable"); assert.equal((await inspect(path, ctx)).sending, true);
  ctx.env.LOOPX_USAGE_PING_ENDPOINT = "https://another.example/v1/ping";
  assert.equal((await inspect(path, ctx)).blocked_by, "notice_required");
});

test("one heartbeat per UTC day; closed-day aggregation is separate and identifier-free", async t => {
  const { path, state } = await fixture(t); const ctx = context();
  await configure(path, ctx, "enable"); const generation = (await state()).generation;
  const sent: { url: string; payload: unknown }[] = [];
  const post: Post = async (url, payload) => { sent.push({ url, payload }); return 204; };
  await observe(path, ctx, generation, row, post); await observe(path, ctx, generation, row, post);
  assert.equal(sent.length, 1); assert.ok(validPing(sent[0].payload));
  assert.equal((await inspect(path, ctx)).aggregate_preview?.counters[0].count, 2);
  await observe(path, context("2026-09-27"), generation, row, post);
  assert.equal(sent.length, 3);
  assert.ok(sent[2].url.endsWith("/aggregate"));
  assert.deepEqual(sent[2].payload, { schema: AGGREGATE_SCHEMA, counters: [{ ...row, count: 2 }] });
  assert.equal((await state()).counters[0].count, 1);
});

test("disable clears ID and pending counts; queued observers and in-flight completion cannot resurrect either", async t => {
  const { path, state } = await fixture(t); const ctx = context();
  await configure(path, ctx, "enable"); const old = await state();
  let release!: () => void; let started!: () => void;
  const startedPromise = new Promise<void>(r => { started = r; });
  const held = new Promise<void>(r => { release = r; });
  const request = observe(path, ctx, old.generation, row, async () => { started(); await held; return 204; });
  await startedPromise; await configure(path, ctx, "disable");
  release(); await request;
  const disabled = await state();
  assert.equal(disabled.consent, "disabled"); assert.equal(disabled.install_id, undefined);
  assert.equal(disabled.counters, undefined); assert.equal(disabled.last_sent_day, undefined);
  await configure(path, ctx, "enable"); const fresh = await state();
  assert.notEqual(fresh.install_id, old.install_id); assert.notEqual(fresh.generation, old.generation);
  await observe(path, ctx, old.generation, row, noPost);
  assert.equal((await state()).last_attempt_day, undefined);
});

test("network failure is lossy and no-retry; no exception text enters local state", async t => {
  const { path, state } = await fixture(t); const ctx = context();
  await configure(path, ctx, "enable"); const generation = (await state()).generation;
  assert.equal((await observe(path, ctx, generation, row, async () => { throw new Error("SECRET:/private/path"); })).sent, false);
  await observe(path, ctx, generation, row, noPost);
  assert.ok(!(await readFile(path, "utf8")).includes("SECRET"));
});

test("malformed state fails closed; disable is the explicit repair", async t => {
  const { path } = await fixture(t); await writeFile(path, "bad");
  await assert.rejects(inspect(path, context()), /usage_state_invalid/);
  await configure(path, context(), "disable");
  assert.equal((await inspect(path, context())).blocked_by, "disabled");
});

test("allowlist rejects content, identity joins and invalid transitions; performance buckets have fixed boundaries", () => {
  const value = { schema: AGGREGATE_SCHEMA, counters: [row] };
  assert.ok(validAggregate(value));
  for (const extra of [{ install_id: "id" }, { path: "/secret" }, { version: "1.0.0" }, { timestamp: "now" }]) assert.equal(validAggregate({ ...value, ...extra }), false);
  for (const bad of [{ ...row, feature: "secret-name" }, { ...row, count: 10001 }, { ...row, error: "raw error" }, { ...row, outcome: "failed" }, { ...row, count: true }]) assert.equal(validAggregate({ ...value, counters: [bad] }), false);
  assert.equal(validAggregate({ ...value, counters: [row, row] }), false);
  assert.deepEqual([0, 100, 1000, 10000, 60000].map(durationBucket), ["lt_100ms", "lt_1s", "lt_10s", "lt_60s", "gte_60s"]);
  for (const url of ["http://remote.example/v1/ping", "https://user:secret@example.com/v1/ping", "https://example.com/v1/ping?q=secret", "https://example.com/v0/ping"]) assert.equal(endpoint({ LOOPX_USAGE_PING_ENDPOINT: url }), "");
});

test("real HTTP sender does not follow redirects to another recipient", async t => {
  let requests = 0;
  const server = createServer((_request, response) => { requests++; response.writeHead(302, { location: "/leak" }); response.end(); });
  await new Promise<void>(r => server.listen(0, "127.0.0.1", r));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const { path, state } = await fixture(t); const ctx = context();
  ctx.env.LOOPX_USAGE_PING_ENDPOINT = `http://127.0.0.1:${(server.address() as { port: number }).port}/v1/ping`;
  await configure(path, ctx, "enable");
  assert.equal((await observe(path, ctx, (await state()).generation, row)).sent, false);
  assert.equal(requests, 1);
});

test("startup heartbeat does not invent a successful command result", async t => {
  const { path, state } = await fixture(t); const ctx = context();
  await configure(path, ctx, "enable");
  const sent: unknown[] = [];
  await observe(path, ctx, (await state()).generation, null, async (_url, payload) => { sent.push(payload); return 204; });
  assert.equal(sent.length, 1);
  assert.equal((await inspect(path, ctx)).aggregate_preview, null);
});

test("a real unresponsive collector times out without retaining error details", async t => {
  const server = createServer(() => {});
  await new Promise<void>(r => server.listen(0, "127.0.0.1", r));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const { path, state } = await fixture(t); const ctx = context();
  ctx.env.LOOPX_USAGE_PING_ENDPOINT = `http://127.0.0.1:${(server.address() as { port: number }).port}/v1/ping`;
  await configure(path, ctx, "enable"); const started = performance.now();
  assert.equal((await observe(path, ctx, (await state()).generation, row)).sent, false);
  assert.ok(performance.now() - started < 4500);
  assert.equal((await state()).last_sent_day, undefined);
});
