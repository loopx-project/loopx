import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { configure, inspect, observe } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import type { Context, Post } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import { union, recordGoalUsage, goalPreview } from "../../loopx/control_plane/runtime/usage_statistics_goals.ts";
import { GOAL_SCHEMA, validGoalAggregate, validGoalObservation, goalDuration } from "../../loopx/control_plane/runtime/usage_statistics_goal_contract.ts";
const base = Date.parse("2026-09-01T12:00:00Z");
const day = 86400000;
const key = "a".repeat(64);
async function fixture(t: test.TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-goal-usage-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return join(root, "usage.json");
}
const ctx = (now: number): Context => ({ env: { LOOPX_USAGE_PING_ENDPOINT: "http://127.0.0.1:1/v1/ping" }, version: "1.0.0", python: "3.13", channel: "source", now: new Date(now) });
const checkpoint = (start: number, end: number) => ({ key, start, end, measurement: "host_call" as const, host: "unknown" as const });

test("union counts concurrent/nested intervals once and preserves idle gaps under replay and reordering", () => {
  const observations: [number, number][] = [[0, 10], [4, 6], [8, 12], [20, 25], [0, 10]];
  for (const order of [observations, [...observations].reverse()]) {
    assert.deepEqual(order.reduce((all, next) => union(all, next), [] as [number, number][]), [[0, 12], [20, 25]]);
  }
});

test("unfinished Goals report once per observed day; concurrent retries add only real execution", async t => {
  const path = await fixture(t);
  await recordGoalUsage(path, "generation", base + 60000, checkpoint(base, base + 60000));
  await recordGoalUsage(path, "generation", base + 90000, checkpoint(base + 30000, base + 90000));
  await recordGoalUsage(path, "generation", base + 90000, checkpoint(base, base + 60000));
  // Retry after a ten-hour pause: span includes the pause, execution does not.
  await recordGoalUsage(path, "generation", base + 10 * 3600000 + 30000, checkpoint(base + 10 * 3600000, base + 10 * 3600000 + 30000));
  const expected = { schema: GOAL_SCHEMA, counters: [{ measurement: "host_call", host: "unknown", span: "lt_1d", duration: "lt_10m", count: 1 }] };
  assert.deepEqual(await goalPreview(path, "generation"), expected);
  assert.deepEqual(await recordGoalUsage(path, "generation", base + day), expected);
  assert.equal(await recordGoalUsage(path, "generation", base + day), null);
  assert.equal(await recordGoalUsage(path, "generation", base + 2 * day), null);
  assert.equal(await goalPreview(path, "generation"), null);
});

test("restart retains measurements, compaction retains totals and rejects old replay", async t => {
  const path = await fixture(t);
  await recordGoalUsage(path, "g", base + 60000, checkpoint(base, base + 60000));
  await recordGoalUsage(path, "g", base + 15 * day, checkpoint(base + 15 * day - 60000, base + 15 * day));
  const state = JSON.parse(await readFile(path, "utf8"));
  assert.equal(state.goals[0].total, 60000);
  assert.equal(state.goals[0].intervals.length, 1);
  await recordGoalUsage(path, "g", base + 15 * day, checkpoint(base, base + 60000));
  assert.equal(JSON.parse(await readFile(path, "utf8")).goals[0].total, 60000);
  assert.deepEqual((await goalPreview(path, "g"))?.counters, [{ measurement: "host_call", host: "unknown", span: "lt_30d", duration: "lt_10m", count: 1 }]);
  // Consent generation reset has no continuity with the previous measurement.
  assert.equal(await goalPreview(path, "new-generation"), null);
});

test("no execution after last confirmed prefix is inferred when host disappears", async t => {
  const path = await fixture(t);
  await recordGoalUsage(path, "g", base + 10000, checkpoint(base, base + 10000));
  const result = await recordGoalUsage(path, "g", base + 5 * day);
  assert.deepEqual(result?.counters, [{ measurement: "host_call", host: "unknown", span: "lt_1m", duration: "lt_1m", count: 1 }]);
  assert.equal(await recordGoalUsage(path, "g", base + 6 * day), null);
  assert.equal(validGoalObservation(checkpoint(base, base + 10 * day), base + 10 * day), false);
  assert.equal(validGoalObservation(checkpoint(base + 10, base), base), false);
});

test("all channels obey consent; disable removes local measurements and rejects stale work", async t => {
  const path = await fixture(t); const now = ctx(base + 60000);
  await configure(path, now, "enable");
  const generation = JSON.parse(await readFile(path, "utf8")).generation;
  const sent: unknown[] = [];
  const post: Post = async (_, payload) => { sent.push(payload); return 204; };
  await observe(path, now, generation, null, post, checkpoint(base, base + 60000));
  assert.ok((await inspect(path, now)).goal_preview);
  await observe(path, ctx(base + day), generation, null, post);
  assert.equal(sent.filter(validGoalAggregate).length, 1);
  const wire = JSON.stringify(sent.filter(validGoalAggregate));
  assert.ok(!wire.includes(key) && !wire.includes(generation) && !wire.includes("install_id"));
  await configure(path, now, "disable");
  await assert.rejects(readFile(path + ".goals"), /ENOENT/);
  await observe(path, ctx(base + 2 * day), generation, null, post, checkpoint(base + 2 * day - 10000, base + 2 * day));
  await assert.rejects(readFile(path + ".goals"), /ENOENT/);
  assert.equal((await inspect(path, now)).goal_preview, null);
});

test("new scope needs notice again without undoing explicit disable", async t => {
  const path = await fixture(t); const now = ctx(base);
  await configure(path, now, "enable");
  const state = JSON.parse(await readFile(path, "utf8"));
  state.notice.version = 1;
  const { writeFile } = await import("node:fs/promises");
  await writeFile(path, JSON.stringify(state));
  assert.equal((await inspect(path, now)).blocked_by, "notice_required");
  await configure(path, now, "disable");
  await configure(path, now, "acknowledge", (await inspect(path, now)).notice);
  assert.equal((await inspect(path, now)).blocked_by, "disabled");
});

test("closed contract rejects identifiers, timestamps and extra fields; long durations are not clipped at a minute", () => {
  const valid = { schema: GOAL_SCHEMA, counters: [{ measurement: "host_call", host: "unknown", span: "gte_30d", duration: "lt_7d", count: 5 }] };
  assert.ok(validGoalAggregate(valid));
  for (const extra of [{ goal_id: "private" }, { install_id: key }, { timestamp: base }]) {
    assert.equal(validGoalAggregate({ ...valid, ...extra }), false);
    assert.equal(validGoalAggregate({ ...valid, counters: [{ ...valid.counters[0], ...extra }] }), false);
  }
  assert.equal(validGoalAggregate({ ...valid, counters: [valid.counters[0], valid.counters[0]] }), false);
  assert.equal(goalDuration(30 * day), "gte_30d");
});

test("late replay after daily claim cannot invent a second observed day", async t => {
  const path = await fixture(t);
  const observation = checkpoint(base, base + 10000);
  await recordGoalUsage(path, "g", base + 10000, observation);
  assert.ok(await recordGoalUsage(path, "g", base + day - 1000, observation));
  assert.equal(await recordGoalUsage(path, "g", base + day), null);
  assert.equal(await goalPreview(path, "g"), null);
});

test("disable during heartbeat prevents a queued Goal aggregate from starting", async t => {
  const path = await fixture(t);
  await configure(path, ctx(base), "enable");
  const generation = JSON.parse(await readFile(path, "utf8")).generation;
  await observe(path, ctx(base + 10000), generation, null, async () => 204, checkpoint(base, base + 10000));
  const sent: string[] = [];
  await observe(path, ctx(base + day), generation, null, async (url) => {
    sent.push(url);
    // Send starts under the lock; asynchronous completion runs outside it.
    await new Promise(resolve => setTimeout(resolve, 10));
    await configure(path, ctx(base + day), "disable");
    return 204;
  });
  assert.deepEqual(sent, ["http://127.0.0.1:1/v1/ping"]);
  await assert.rejects(readFile(path + ".goals"), /ENOENT/);
});
