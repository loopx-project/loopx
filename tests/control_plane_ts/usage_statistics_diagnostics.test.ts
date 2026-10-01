import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { configure, inspect, observe } from "../../loopx/control_plane/runtime/usage_statistics.ts";
import { DIAGNOSTIC_SCHEMA, resultDiagnostic, usageContext, validDiagnostics } from "../../loopx/control_plane/runtime/usage_statistics_diagnostics.ts";
import type { Diagnostic } from "../../loopx/control_plane/runtime/usage_statistics_diagnostics.ts";

const row: Diagnostic = { feature: "pr-review", operation: "merge-readiness", outcome: "blocked", error: "not_ready", duration: "lt_1s", count: 1,
  version: "1.2.3", activity_day: "2026-09-30", context: "unknown", signal: "none" };
test("readiness holds are not failures; errors and unknown operations remain bounded", () => {
  assert.deepEqual(resultDiagnostic("pr-review", "merge-readiness", { ok: true, ready: false }, 1, undefined),
    { operation: "merge-readiness", outcome: "blocked", error: "not_ready", signal: "none" });
  assert.equal(resultDiagnostic("turn", "run-once", { ok: false }, 1, "invalid_input").error, "invalid_input");
  assert.equal(resultDiagnostic("turn", "secret/project/path", { error: "secret" }, 1, "secret").operation, "default");
  assert.equal(usageContext("organization-name"), "unknown");
});
test("signals require committed changed evidence, never mere exit success, prose or replay", () => {
  assert.equal(resultDiagnostic("todo", "complete", { ok: true }, 0, undefined).signal, "none");
  assert.equal(resultDiagnostic("todo", "complete", { ok: true, completed: true, changed: false, validation_passed: true }, 0, undefined).signal, "none");
  assert.equal(resultDiagnostic("todo", "complete", { ok: true, completed: true, changed: true, validation_passed: true }, 0, undefined).signal, "todo_validated");
  assert.equal(resultDiagnostic("turn", "run-once", { ok: true, turn_committed: true }, 0, undefined).signal, "managed_turn_committed");
  assert.equal(resultDiagnostic("other", "result-return", { ok: true, changed: true, reply_verified: false }, 0, undefined).signal, "none");
  assert.equal(resultDiagnostic("other", "result-return", { ok: true, changed: true, reply_verified: true }, 0, undefined).signal, "result_returned");
});
test("expanded contract rejects identity, raw errors, arbitrary fields and illegal result combinations", () => {
  const payload = { schema: DIAGNOSTIC_SCHEMA, counters: [row] };
  assert.ok(validDiagnostics(payload));
  for (const field of ["install_id", "goal_id", "host", "ip", "timestamp", "arguments"]) {
    assert.equal(validDiagnostics({ ...payload, [field]: "private" }), false);
    assert.equal(validDiagnostics({ ...payload, counters: [{ ...row, [field]: "private" }] }), false);
  }
  for (const changes of [{ error: "raw text" }, { context: "company" }, { activity_day: "2026-02-31" }, { outcome: "ok" }, { signal: "todo_validated" }, { operation: "complete" }, { feature: "turn" }]) {
    assert.equal(validDiagnostics({ ...payload, counters: [{ ...row, ...changes }] }), false);
  }
});
test("new diagnostics flush once, preserve activity date, renew v4 notice and obey opt-out", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-diagnostics-")); t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "state.json");
  const ctx = { env: { LOOPX_USAGE_PING_ENDPOINT: "http://127.0.0.1:1/v1/ping" }, version: "1.2.3", python: "3.13", channel: "source", now: new Date("2026-10-01T00:02:00Z") };
  await configure(path, ctx, "enable");
  const old = JSON.parse(await readFile(path, "utf8")); old.notice.version = 4;
  old.diagnostics = [row]; await writeFile(path, JSON.stringify(old));
  await observe(path, ctx, old.generation, null, async () => { assert.fail("old disclosure"); }, undefined, undefined, row);
  assert.equal((await inspect(path, ctx)).blocked_by, "notice_required");
  await configure(path, ctx, "acknowledge", (await inspect(path, ctx)).notice);
  const state = JSON.parse(await readFile(path, "utf8")); const sent: unknown[] = [];
  assert.equal(state.install_id, old.install_id); assert.notEqual(state.generation, old.generation);
  assert.deepEqual(state.diagnostics, []);
  await observe(path, ctx, state.generation, null, async (_url, value) => { sent.push(value); return 204; }, undefined, undefined, row);
  assert.deepEqual(sent[1], { schema: DIAGNOSTIC_SCHEMA, counters: [row] });
  await observe(path, ctx, state.generation, null, async () => { assert.fail("interval must hold"); }, undefined, undefined, row);
  assert.equal((await inspect(path, ctx)).diagnostic_preview?.counters[0].count, 1);
  await configure(path, ctx, "disable");
  await observe(path, ctx, state.generation, null, async () => { assert.fail("disabled"); }, undefined, undefined, row);
  assert.equal((await inspect(path, ctx)).diagnostic_preview, null);
});

test("diagnostic diversity is bounded and stale activity cannot reach a later batch", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-diagnostic-capacity-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "state.json");
  const ctx = { env: {}, version: "1.2.3", python: "3.13", channel: "source", now: new Date("2026-09-30T12:00:00Z") };
  await configure(path, ctx, "enable");
  const state = JSON.parse(await readFile(path, "utf8"));
  const sent: unknown[] = [];
  const post = async (_url: string, value: unknown) => { sent.push(value); return 204; };
  await observe(path, ctx, state.generation, null, post, undefined, undefined, row);
  sent.length = 0;
  for (let n = 0; n < 34; n++) {
    await observe(path, ctx, state.generation, null, post, undefined, undefined, { ...row, version: `1.2.${100 + n}` });
  }
  const full = await inspect(path, ctx);
  assert.equal(sent.length, 0, "diversity must not bypass the delivery interval");
  assert.equal(full.diagnostic_preview?.counters.length, 32);
  assert.equal(full.diagnostic_dropped, 2);
  const later = { ...ctx, now: new Date("2026-10-08T12:00:00Z") };
  const fresh = { ...row, activity_day: "2026-10-08" };
  await observe(path, later, state.generation, null, post, undefined, undefined, fresh);
  assert.deepEqual(sent[1], { schema: DIAGNOSTIC_SCHEMA, counters: [fresh] });
  assert.equal((await inspect(path, later)).diagnostic_preview, null);
});
