import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { configure, configureContext, inspect, observe } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import type { Context, Post } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import { recordInstallation, installationPreview } from "../../loopx/control_plane/runtime/usage_statistics_installation.ts";
import { validInstallationUsage, profileFeature } from "../../loopx/control_plane/runtime/usage_statistics_installation_contract.ts";
import type { GoalObservation } from "../../loopx/control_plane/runtime/usage_statistics_goal_contract.ts";
import { acquireFileMutationLock, releaseFileMutationLock } from "../../loopx/control_plane/effect_runtime_io.ts";
const id = "00000000-0000-4000-8000-000000000001";
const at = (value: string) => Date.parse("2026-10-" + value + "Z");
const observation = (start: number, end: number, key = "a", measurement: GoalObservation["measurement"] = "codex_turn"): GoalObservation =>
  ({ key: key.repeat(64), start, end, measurement, host: "codex_app" });
async function fixture(t: test.TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-installation-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return join(root, "usage.json");
}
test("device context is shared, environment wins, and labeling never enables or rotates identity", async t => {
  const path = await fixture(t);
  const ctx: Context = { env: {}, version: "1.2.4", python: "3.13", channel: "source" };
  await configureContext(path, ctx, "maintainer");
  assert.equal((await inspect(path, ctx)).sending, false);
  assert.equal((await inspect(path, ctx)).effective_context, "maintainer");
  assert.equal((await inspect(path, ctx)).context_source, "device");
  assert.equal((await inspect(path, { ...ctx, env: { LOOPX_USAGE_CONTEXT: "personal" } })).effective_context, "personal");
  assert.equal((await inspect(path, { ...ctx, env: { LOOPX_USAGE_CONTEXT: "invalid" } })).effective_context, "unknown");
  await assert.rejects(configureContext(path, ctx, "company-name"));
  await configure(path, ctx, "enable");
  const original = (await inspect(path, ctx)).next_payload?.install_id;
  await configureContext(path, ctx, "personal");
  assert.equal((await inspect(path, ctx)).next_payload?.install_id, original);
  await configure(path, ctx, "disable");
  await configureContext(path, ctx, "maintainer");
  assert.equal((await inspect(path, ctx)).blocked_by, "disabled");
  assert.equal((await inspect(path, ctx)).next_payload, null);
});
test("installation union crosses Goals and midnight, excludes idle gaps and keeps clocks separate", async t => {
  const path = await fixture(t), now = at("02T01:00:00");
  await recordInstallation(path, id, id, now, "1.2.4", "personal", "turn", [
    observation(at("01T23:30:00"), at("02T00:30:00")),
    observation(at("02T00:00:00"), at("02T00:45:00"), "b"),
    observation(at("02T00:50:00"), at("02T00:51:30"), "c", "host_call"),
  ]);
  const saved = JSON.parse(await readFile(path, "utf8"));
  assert.deepEqual(saved.days.find((row: {activity_day: string}) => row.activity_day === "2026-10-01").runtime,
    [{ measurement: "codex_turn", observed_minutes: 30 }]);
  assert.deepEqual(saved.days.find((row: {activity_day: string}) => row.activity_day === "2026-10-02").runtime,
    [{ measurement: "codex_turn", observed_minutes: 45 }, { measurement: "host_call", observed_minutes: 1 }]);
  const replay = await recordInstallation(path, id, id, now + 16 * 60000, "1.2.4", "maintainer", undefined, [
    observation(at("01T23:30:00"), at("02T00:30:00")), observation(at("02T00:00:00"), at("02T00:45:00"), "b"),
  ]);
  assert.equal(replay, null);
  assert.equal((await installationPreview(path, id, id)), null);
  assert.ok(saved.days.every((row: {context: string}) => row.context === "personal"), "history is not relabeled");
});

test("each daily clock agrees with an independent endpoint sweep, including sub-minute fragments", async t => {
  const observations: GoalObservation[] = [];
  const midnight=at("02T00:00:00"), now=midnight+600000;
  for(const measurement of ["host_call","codex_turn","quota_cycle"] as const) {
    observations.push(observation(midnight-60000,midnight+60000,"a",measurement));
    observations.push(observation(midnight-30000,midnight+30000,"b",measurement));
    observations.push(observation(midnight+120000,midnight+149000,"c",measurement));
    observations.push(observation(midnight+180000,midnight+211000,"d",measurement));
  }
  // Unlike the product's interval-merging algorithm, this oracle sweeps signed
  // endpoints. Duration is integrated while population > 0, then rounded once.
  const expected = (measurement: GoalObservation["measurement"], day: number) => {
    const points = observations.filter(o=>o.measurement===measurement).flatMap(o=> {
      const start=Math.max(o.start,day), end=Math.min(o.end,day+86400000);
      return end>start ? [[start,1],[end,-1]] as [number,number][] : [];
    }).sort(([a],[b])=>a-b);
    let active=0, previous=day, elapsed=0;
    for(const [time,delta] of points) { if(active>0) elapsed+=time-previous; active+=delta; previous=time; }
    return Math.floor(elapsed/60000);
  };
  for(const ordered of [observations,[...observations].reverse()]) {
    const path=await fixture(t);
    await recordInstallation(path,id,id,now,"1.2.4","maintainer",undefined,ordered);
    const before=JSON.parse(await readFile(path,"utf8"));
    await recordInstallation(path,id,id,now,"1.2.4","maintainer",undefined,ordered);
    assert.deepEqual(JSON.parse(await readFile(path,"utf8")),before,"runtime replay changes neither totals nor revision");
    for(const row of before.days) {
      for(const clock of row.runtime) {
        assert.equal(clock.observed_minutes,expected(clock.measurement,Date.parse(row.activity_day)));
      }
    }
    assert.ok(before.days.find((row:{activity_day:string})=>row.activity_day==="2026-10-02").runtime.every((clock:{observed_minutes:number})=>clock.observed_minutes===2));
  }
});
test("restart, partial updates and expiration do not extrapolate missing runtime", async t => {
  const path = await fixture(t), now = at("01T12:00:00");
  const first = await recordInstallation(path, id, id, now, "1.2.4", "maintainer", "memory", []);
  assert.ok(validInstallationUsage(first));
  assert.deepEqual(first!.profiles[0].runtime, [], "no observed runtime is unknown, not zero uptime");
  await recordInstallation(path, id, id, now + 60000, "1.2.4", "personal", "state", []);
  const later = await recordInstallation(path, id, id, now + 16 * 60000, "1.2.4", "personal", undefined, []);
  assert.ok(validInstallationUsage(later));
  assert.deepEqual(later!.profiles[0].cli, [{ feature: "memory", count: 1 }, { feature: "state", count: 1 }]);
  assert.equal(later!.profiles[0].context, "maintainer");
  const expired = await recordInstallation(path, id, id, at("10T12:00:00"), "1.2.4", "personal", "todo", []);
  assert.equal(expired!.profiles.length, 1);
  assert.equal(expired!.profiles[0].context, "personal");
});
test("expanded fixed families reject custom names and transport rejects content or impossible daily time", () => {
  assert.equal(profileFeature("refresh-state"), "state");
  assert.equal(profileFeature("heartbeat-prompt"), "heartbeat");
  assert.equal(profileFeature("reward-memory"), "memory");
  assert.equal(profileFeature("my-private-plugin"), "other");
  const profile = { activity_day: "2026-10-01", version: "1.2.4", context: "unknown", revision: 1,
    cli: [], runtime: [{ measurement: "quota_cycle", observed_minutes: 60 }], truncated: false };
  const payload = { schema: "loopx_installation_usage_v1", install_id: id, profiles: [profile] };
  assert.ok(validInstallationUsage(payload));
  assert.equal(validInstallationUsage({ ...payload, goal: "private" }), false);
  assert.equal(validInstallationUsage({ ...payload, profiles: [{ ...profile, runtime: [{ measurement: "quota_cycle", observed_minutes: 1441 }] }] }), false);
});
test("suppression and disable fence all new channels and erase local interval history", async t => {
  const path = await fixture(t);
  const ctx: Context = { env: {}, version: "1.2.4", python: "3.13", channel: "source", now: new Date(at("01T12:00:00")) };
  await configureContext(path, ctx, "maintainer"); await configure(path, ctx, "enable");
  const generation = JSON.parse(await readFile(path, "utf8")).generation;
  const sent: unknown[] = [], post: Post = async (_url, payload) => { sent.push(payload); return 204; };
  await observe(path, ctx, generation, null, post, observation(at("01T11:00:00"), at("01T12:00:00")));
  assert.ok(sent.some(payload => validInstallationUsage(payload) && payload.profiles[0].context === "maintainer"));
  const stateBefore = await readFile(path + ".installation", "utf8");
  await observe(path, { ...ctx, env: { CI: "true" } }, generation, null, async () => assert.fail("CI must not send"));
  assert.equal(await readFile(path + ".installation", "utf8"), stateBefore);
  await configure(path, ctx, "disable");
  await assert.rejects(readFile(path + ".installation"), /ENOENT/);
  await observe(path, ctx, generation, null, async () => assert.fail("stale observer must not send"));
  assert.equal((await inspect(path, ctx)).installation_preview, null);
  assert.equal((await inspect(path, ctx)).stored_context, "maintainer");
});
test("short-lived CLI completion survives a competing detached startup observation", async t => {
  const path = await fixture(t);
  const ctx: Context = { env: {}, version: "1.2.4", python: "3.13", channel: "source" };
  await configure(path, ctx, "enable");
  const generation = JSON.parse(await readFile(path, "utf8")).generation;
  const sent: Parameters<Post>[1][] = [];
  const send: Post = async (_url, payload) => { sent.push(payload); return 204; };
  // The daily startup worker and command-completion worker share this local
  // telemetry lock. A briefly occupied lock must not lose the first CLI count.
  const lock = await acquireFileMutationLock(path);
  const completed = observe(path, ctx, generation, null, send, undefined, undefined, undefined, "version")
    .then(value => ({ value }), error => ({ error }));
  await new Promise(resolve => setTimeout(resolve, 40));
  await releaseFileMutationLock(path, lock.token);
  const result = await completed;
  assert.ok("value" in result, "detached observer must wait for a short telemetry-only lock hold");
  await observe(path, ctx, generation, null, send);
  const profile = sent.find(validInstallationUsage);
  assert.deepEqual(profile?.profiles[0].cli, [{ feature: "version", count: 1 }]);
  assert.equal(sent.filter(row => row.schema === "loopx_usage_ping_v1").length, 1);
});
