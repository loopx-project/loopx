import assert from "node:assert/strict";
import test from "node:test";
import {readFile, readdir, writeFile, unlink} from "node:fs/promises";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {drainShadowOutbox, SHADOW_DRAIN_SCHEMA} from "../../loopx/control_plane/coordination/shadow_drain.ts";
import {commitLocalAuthorityShadowEntry} from "../../loopx/control_plane/coordination/local_authority_shadow.ts";
import {fixture, pendingEntry, todo, type ShadowFixture} from "./shadow_file_fixture.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";

const python = resolveTestPython();
function request(f: ShadowFixture, extra: JsonObject = {}): JsonObject {
  return {schema_version: SHADOW_DRAIN_SCHEMA, runtime_root: f.root, goal_id: "goal-a", python_executable: python,
    config_enabled: true, max_entries: 256, budget_seconds: 30, lock_timeout_seconds: 1, ...extra};
}
const directory = (f: ShadowFixture) => join(f.root, "authority-shadow/outbox/goal-a/todos");
async function inventory(f: ShadowFixture) {
  return Object.fromEntries(await Promise.all((await readdir(directory(f))).sort().map(async name => [name, await readFile(join(directory(f), name), "utf8")])));
}

test("complete batch retains mixed Todo metadata and returns no history across transport", async t => {
  const f = await fixture(t), mixed = productionScaleCoordinationFixture("goal-a", "native");
  const todos = mixed.projection.todos as JsonObject[];
  await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos});
  const result = await drainShadowOutbox(request(f));
  assert.equal(result.ok, true, JSON.stringify(result)); assert.equal(result.delivered, 1);
  assert.equal(result.pending_after, 0); assert.equal(result.reclaimed_residue, 2);
  assert.ok(Buffer.byteLength(JSON.stringify(result)) < 4096);
  assert.equal(Object.hasOwn(result, "projection"), false);
  const loaded = await f.store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status === "loaded") assert.deepEqual(loaded.head.todos, todos);
  assert.equal((await drainShadowOutbox(request(f))).outcome, "nothing_pending");
});

for (const phase of ["after_commit", "after_cursor", "after_unlink"] as const) {
  test(`failure at ${phase} replays receipt without another commit`, async t => {
    const f = await fixture(t);
    await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
    let failed = false;
    const first = await drainShadowOutbox(request(f), {afterEffect: async p => {
      if (p === phase && !failed) {failed = true; throw new Error("injected IO interruption");}
    }});
    assert.equal(first.ok, false); assert.equal(failed, true);
    const before = await f.store.scanCommitted(null, 10);
    const second = await drainShadowOutbox(request(f));
    assert.equal(second.ok, true, JSON.stringify(second)); assert.equal(second.replayed, 1); assert.equal(second.delivered, 0);
    assert.deepEqual(await f.store.scanCommitted(null, 10), before);
    assert.deepEqual(await readdir(directory(f)), ["drain-cursor.json"]);
  });
}

for (const mutation of ["entry", "cursor", "new_entry"] as const) {
  test(`filesystem ${mutation} changed after proof cannot be cleaned`, async t => {
    const f = await fixture(t);
    const entry = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
    assert.equal((await commitLocalAuthorityShadowEntry(entry)).outcome, "delivered");
    let changed = false, after: Record<string, string> = {};
    const result = await drainShadowOutbox(request(f), {afterEffect: async phase => {
      if (phase !== "after_proof" || changed) return;
      changed = true;
      if (mutation === "entry") {
        const path = join(directory(f), (await readdir(directory(f))).find(n => n.endsWith("prepared.json"))!);
        await writeFile(path, (await readFile(path, "utf8")) + "\n");
      } else if (mutation === "cursor") await writeFile(join(directory(f), "drain-cursor.json"), "{}");
      else await pendingEntry(f, 2, {handoff_mode: "hard_lease", todos: [todo(), todo("second")]});
      after = await inventory(f);
    }});
    assert.equal(result.ok, false); assert.equal(result.reclaimed_residue, 0);
    assert.deepEqual(await inventory(f), after);
  });
}

test("deadline expiring in proof keeps recoverable evidence", async t => {
  const f = await fixture(t);
  const entry = await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
  await commitLocalAuthorityShadowEntry(entry);
  const before = await inventory(f);
  const result = await drainShadowOutbox(request(f, {budget_seconds: .05}), {afterEffect: async phase => {
    if (phase === "after_proof") await new Promise(resolve => setTimeout(resolve, 60));
  }});
  assert.equal(result.budget_exhausted, true); assert.equal(result.reclaimed_residue, 0);
  assert.deepEqual(await inventory(f), before);
  assert.equal((await drainShadowOutbox(request(f))).replayed, 1);
});

test("one-entry budget leaves the remaining source writes intact", async t => {
  const f = await fixture(t);
  const one = {handoff_mode: "hard_lease", todos: [todo()]};
  await pendingEntry(f, 1, one);
  await pendingEntry(f, 2, {handoff_mode: "hard_lease", todos: [todo(), todo("two")]}, {previousPartitionProjection: one});
  const first = await drainShadowOutbox(request(f, {max_entries: 1}));
  assert.equal(first.delivered, 1, JSON.stringify(first)); assert.equal(first.pending_after, 1);
  const second = await drainShadowOutbox(request(f));
  assert.equal(second.delivered, 1); assert.equal(second.pending_after, 0);
});

test("a corrupt candidate tail stops ordered delivery and remains repairable", async t => {
  const f = await fixture(t), one = {handoff_mode: "hard_lease", todos: [todo()]};
  await pendingEntry(f, 1, one);
  await pendingEntry(f, 2, {handoff_mode: "hard_lease", todos: [todo(), todo("two")]}, {previousPartitionProjection: one});
  let count = 0, saved: Buffer | null = null;
  const result = await drainShadowOutbox(request(f), {afterEffect: async phase => {
    if (phase === "before_commit" && ++count === 2) {saved = await readFile(f.store.path); await writeFile(f.store.path, "{corrupt");}
  }});
  assert.equal(result.ok, false); assert.equal(result.delivered, 1); assert.equal(result.pending_after, 1);
  assert.ok(saved); await writeFile(f.store.path, saved);
  assert.equal((await drainShadowOutbox(request(f))).delivered, 1);
});

test("unproved committed-only residue never becomes successful cleanup", async t => {
  const f = await fixture(t);
  await pendingEntry(f, 1, {handoff_mode: "hard_lease", todos: [todo()]});
  const name = (await readdir(directory(f))).find(n => n.endsWith("prepared.json"))!;
  await unlink(join(directory(f), name)); const before = await inventory(f);
  assert.equal((await drainShadowOutbox(request(f))).ok, false);
  assert.deepEqual(await inventory(f), before);
});
