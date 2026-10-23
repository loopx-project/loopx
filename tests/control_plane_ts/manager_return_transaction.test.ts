import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { RETURN_DELIVERY_TRANSACTION, transactManagerReturnDelivery } from "../../loopx/control_plane/collaboration/return_delivery.ts";

const id = "a".repeat(64);
const now = "2026-01-02T03:04:05.123456+00:00";
const ref = { goal_id: "research", goal_instance_id: "ginst_" + "b".repeat(32) };
const attempt = { schema_version: "manager_return_delivery_attempt_v0", provider: "lark",
  message_ref: "om_result", intent_digest: "sha256:" + "c".repeat(64),
  provider_receipt: "sha256:" + "d".repeat(64) };

async function fixture(exact: boolean, external = false) {
  const root = await mkdtemp(join(tmpdir(), "loopx-return-transaction-"));
  const folder = join(root, ".local", "manager-context");
  const entry: JsonObject = { request_id: id, goal_id: "research", agent_id: "worker", source_id: "source:original",
    ...(exact ? { goal_ref: ref } : {}) };
  const route = { ...entry, session_id: "session", client_turn_id: "original", channel_id: external ? "manager.external.fixture" : "manager" };
  const reply = { ...entry, decision: "adopt", phase: "conclusion", text: "Independent findings." };
  const entryPath = join(folder, "entries", "e".repeat(64), id + ".json");
  const statePath = join(folder, "replies", id, "conclusion.delivery.json");
  async function put(path: string, body: unknown) {
    await mkdir(join(path, ".."), { recursive: true });
    await writeFile(path, JSON.stringify(body));
  }
  await put(entryPath, entry);
  await put(join(folder, "roundtrips", id + ".json"), route);
  await put(join(folder, "replies", id, "conclusion.json"), reply);
  const facts: JsonObject = { entry, entry_path: entryPath, route,
    session: { session_id: "session", goal_id: "loopx-manager", channel_id: route.channel_id, status: "open" },
    turn: { turn_id: "turn", client_turn_id: "original", status: "completed" },
    grant: { source_id: entry.source_id, targets: [{ goal_id: entry.goal_id, agent_id: entry.agent_id }] },
    scope: { profile_id: exact ? "source_session_v1" : null,
      caller_goal_ref: exact ? ref : null, current_goal_ref: exact ? ref : null },
    initial_delivery: { request: entry, route, authorized_source_id: entry.source_id,
      turn: { client_turn_id: "original", status: "completed" } } };
  const command = { schema_version: RETURN_DELIVERY_TRANSACTION, runtime_root: root, request_id: id,
    result_key: "conclusion", exact, now, facts };
  return { root, folder, entryPath, statePath, facts, command, put,
    state: async () => JSON.parse(await readFile(statePath, "utf8")),
    run: (phase: string, fields: JsonObject = {}) => transactManagerReturnDelivery({ ...command, phase, ...fields }),
    close: () => rm(root, { recursive: true, force: true }) };
}

for (const exact of [false, true]) {
  test(`private return settles once and retains the original message identity (${exact})`, async () => {
    const f = await fixture(exact);
    try {
      const plan = await f.run("prepare");
      assert.equal(plan.action, "private");
      assert.match(String(plan.message_id), /^handoff\.[a-f0-9]{64}$/);
      const settled = await f.run("settle", { ...plan, outcome: { kind: "private" } });
      assert.equal(settled.processed, 1);
      assert.equal((await f.state()).status, "delivered");
      const bytes = await readFile(f.statePath);
      assert.equal((await f.run("prepare")).action, "skip");
      assert.deepEqual(await readFile(f.statePath), bytes);
    } finally { await f.close(); }
  });

  test(`provider acceptance survives response loss and resumes by verification (${exact})`, async () => {
    const f = await fixture(exact, true);
    try {
      const plan = await f.run("prepare");
      assert.equal(plan.action, "send");
      await f.run("attempt", { ...plan, attempt });
      const resumed = await f.run("prepare", { now: "2026-01-02T03:06:05.123456+00:00" });
      assert.equal(resumed.action, "verify");
      assert.deepEqual(resumed.attempt, attempt);
      await f.run("settle", { ...resumed, now: "2026-01-02T03:06:05.123456+00:00", outcome: { kind: "verified",
        verification: { verification_performed: true, reply_verified: true } } });
      assert.equal((await f.state()).status, "delivered");
      assert.equal((await f.state()).provider_receipt, attempt.provider_receipt);
      assert.equal((await f.run("prepare")).action, "skip");
    } finally { await f.close(); }
  });

  test(`an accepted write without a locator is terminal and retains its evidence (${exact})`, async () => {
    const f = await fixture(exact, true);
    try {
      const plan = await f.run("prepare");
      const locatorless = { ...attempt, message_ref: null };
      const saved = await f.run("attempt", { ...plan, attempt: locatorless });
      await f.run("settle", { ...saved, outcome: { kind: "sent", external_write_performed: true } });
      assert.equal((await f.state()).status, "explicit_unverified");
      assert.deepEqual((await f.state()).attempt, locatorless);
      assert.equal((await f.run("prepare")).action, "skip");
    } finally { await f.close(); }
  });

  test(`corrupt delivery evidence and changed source bytes cannot be overwritten (${exact})`, async () => {
    const f = await fixture(exact);
    try {
      await writeFile(f.statePath, "{broken");
      assert.equal((await f.run("prepare")).action, "skip");
      assert.equal(await readFile(f.statePath, "utf8"), "{broken");
      await f.put(f.statePath, { status: "unknown_delivery_state" });
      assert.equal((await f.run("prepare")).action, "skip");
      assert.deepEqual(await f.state(), { status: "unknown_delivery_state" });
      await rm(f.statePath);
      await f.put(f.entryPath, { ...(f.facts.entry as JsonObject), source_id: "another-source" });
      assert.equal((await f.run("prepare")).action, "skip");
      if (exact) await assert.rejects(readFile(f.statePath));
      else assert.equal((await f.state()).status, "retry_pending");
    } finally { await f.close(); }
  });

  test(`a malformed saved attempt never supplies an external verification address (${exact})`, async () => {
    const f = await fixture(exact, true);
    try {
      const corrupt = { ...attempt, intent_digest: "not-a-digest" };
      await f.put(f.statePath, { status: "verification_required", attempt: corrupt });
      const plan = await f.run("prepare");
      assert.equal(plan.action, "verify");
      assert.equal(plan.attempt, null);
      await f.run("settle", { ...plan, outcome: { kind: "locator_unavailable" } });
      assert.equal((await f.state()).status, "explicit_unverified");
      assert.deepEqual((await f.state()).attempt, corrupt);
    } finally { await f.close(); }
  });
}

test("concurrent exact admission is single flight and stale settlement cannot overwrite a checkpoint", async () => {
  const f = await fixture(true, true);
  try {
    const plans = await Promise.all([f.run("prepare"), f.run("prepare")]);
    assert.equal(plans.filter(plan => plan.action === "send").length, 1);
    const plan = plans.find(plan => plan.action === "send")!;
    await f.run("attempt", { ...plan, attempt });
    const bytes = await readFile(f.statePath);
    await assert.rejects(f.run("settle", { ...plan, outcome: { kind: "sent", reply_verified: true } }), /state changed/);
    assert.deepEqual(await readFile(f.statePath), bytes);
  } finally { await f.close(); }
});

test("an ambiguous expired external admission never dispatches another send", async () => {
  const f = await fixture(true, true);
  try {
    assert.equal((await f.run("prepare")).action, "send");
    assert.equal((await f.run("prepare", { now: "2026-01-02T03:06:05.123456+00:00" })).action, "skip");
    assert.equal((await f.state()).status, "explicit_unverified");
    assert.equal((await f.state()).error, "provider_delivery_unverified");
  } finally { await f.close(); }
});

test("invalid transaction addresses and phases refuse before changing evidence", async () => {
  const f = await fixture(false);
  try {
    for (const fields of [{ request_id: "../escape" }, { result_key: "../escape" },
      { schema_version: "unsupported" }, { phase: "unknown" }, { now: "not-a-clock" }]) {
      await assert.rejects(transactManagerReturnDelivery({ ...f.command, phase: "prepare", ...fields }));
    }
    await assert.rejects(readFile(f.statePath));
  } finally { await f.close(); }
});

test("scan skips terminal and corrupt records but locked admission rechecks every hint", async () => {
  const f = await fixture(false);
  const scan = (candidates = [{ request_id: id, result_key: "conclusion" }]) =>
    transactManagerReturnDelivery({ ...f.command, phase: "scan", candidates });
  try {
    assert.deepEqual((await scan()).candidates, [{ request_id: id, result_key: "conclusion" }]);
    await f.put(f.statePath, { status: "delivered" });
    assert.deepEqual((await scan()).candidates, []);
    assert.equal((await f.run("prepare")).action, "skip");
    await writeFile(f.statePath, "{broken");
    const unreadable = await scan();
    assert.deepEqual(unreadable.candidates, []);
    assert.equal(unreadable.unavailable_count, 1);
    assert.equal(await readFile(f.statePath, "utf8"), "{broken");
    await assert.rejects(scan(Array(129).fill({ request_id: id, result_key: "conclusion" })), /at most 128/);
    await assert.rejects(transactManagerReturnDelivery({ ...f.command, phase: "scan", schema_version: "old", candidates: [] }));
  } finally { await f.close(); }
});

test("provider exception after a locatorless checkpoint cannot become a retry", async () => {
  const f = await fixture(false, true);
  try {
    const plan = await f.run("prepare");
    const saved = await f.run("attempt", { ...plan, attempt: { ...attempt, message_ref: null } });
    await f.run("settle", { ...saved, outcome: { kind: "error" } });
    assert.equal((await f.state()).status, "explicit_unverified");
    assert.equal((await f.state()).error, "provider_locator_unavailable");
    assert.equal((await f.run("prepare")).action, "skip");
  } finally { await f.close(); }
});

test("scan isolates malformed addresses while retaining transaction envelope validation", async () => {
  const f = await fixture(false);
  try {
    const result = await f.run("scan", {candidates: [
      {request_id: "!orphan", result_key: "conclusion"}, null,
      {request_id: id, result_key: "../escape"}, {request_id: id, result_key: "conclusion"},
    ]});
    assert.deepEqual(result.candidates, [{request_id: id, result_key: "conclusion"}]);
    assert.equal(result.unavailable_count, 3);
    await assert.rejects(f.run("scan", {runtime_root: "relative", candidates: [
      {request_id: id, result_key: "conclusion"},
    ]}), /absolute/);
    await assert.rejects(readFile(f.statePath));
  } finally { await f.close(); }
});

for (const missing of ["session", "turn"]) {
  test(`accepted legacy attempt terminates when the original ${missing} is absent`, async () => {
    const f = await fixture(false, true);
    try {
      await f.put(f.statePath, {status: "verification_required", attempt});
      const facts = {...f.facts, [missing]: null};
      assert.equal((await f.run("prepare", {facts})).action, "skip");
      assert.equal((await f.state()).status, "explicit_unverified");
      assert.equal((await f.state()).error, "original_route_unavailable");
      assert.deepEqual((await f.state()).attempt, attempt);
      assert.equal((await f.run("prepare", {facts})).processed, 0);
    } finally { await f.close(); }
  });
}

test("exact verifier failure retains its provider diagnostic and accepted attempt", async () => {
  const f = await fixture(true, true);
  try {
    const plan = await f.run("prepare");
    await f.run("attempt", {...plan, attempt});
    const resumed = await f.run("prepare", {now: "2026-01-02T03:06:05.123456+00:00"});
    await f.run("settle", {...resumed, now: "2026-01-02T03:06:05.123456+00:00",
      outcome: {kind: "verification_error", blocker: null}});
    assert.equal((await f.state()).status, "retry_pending");
    assert.equal((await f.state()).error, "provider_verification_unavailable");
    assert.deepEqual((await f.state()).attempt, attempt);
    assert.equal((await f.run("prepare", {now: "2026-01-02T03:08:05.123456+00:00"})).action, "verify");
  } finally { await f.close(); }
});

test("invalid UTF-8 and unexpected BOMs cannot become a repaired receipt or delivery instruction", async () => {
  const f = await fixture(false);
  try {
    for (const bytes of [Buffer.from([0x7b, 0x22, 0xff, 0x22, 0x7d]), Buffer.from('\ufeff{"status":"queued"}')]) {
      await writeFile(f.statePath, bytes);
      assert.equal((await f.run("prepare")).action, "skip");
      assert.deepEqual(await readFile(f.statePath), bytes);
    }
  } finally { await f.close(); }
});

test("a surrogate result cannot be silently replaced while hashing or delivering", async () => {
  const f = await fixture(false);
  try {
    const path = join(f.folder, "replies", id, "conclusion.json");
    const reply = JSON.parse(await readFile(path, "utf8"));
    await f.put(path, { ...reply, text: "An incomplete character: \ud800" });
    const bytes = await readFile(path);
    assert.equal((await f.run("prepare")).action, "skip");
    assert.deepEqual(await readFile(path), bytes);
  } finally { await f.close(); }
});


for (const [clock, retry] of [
  ["2026-01-02T03:04:05.123456", "2026-01-02T03:04:15.123456"],
  ["2026-01-02T23:59:55+08:00", "2026-01-03T00:00:05+08:00"],
  ["2026-01-02T03:04:05.123456+08:00:30", "2026-01-02T03:04:15.123456+08:00:30"],
]) {
  test(`retry retains the Python clock's offset and fractional precision (${clock})`, async () => {
    const f = await fixture(false, true);
    try {
      const plan = await f.run("prepare", { now: clock });
      await f.run("settle", { ...plan, now: clock, outcome: { kind: "sender_unavailable" } });
      assert.equal((await f.state()).retry_at, retry);
    } finally { await f.close(); }
  });
}
