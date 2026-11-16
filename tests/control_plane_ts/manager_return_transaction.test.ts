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

test("a legacy transaction cannot rewrite a saved exact attempt", async () => {
  const f = await fixture(true, true);
  try {
    const plan = await f.run("prepare");
    const saved = await f.run("attempt", { ...plan, attempt });
    const before = await readFile(f.statePath);
    const legacy = { exact: false, now: "2026-01-02T03:06:05.123456+00:00",
      facts: { error: "return_source_unavailable" } };
    const scanned = await f.run("scan", { ...legacy, candidates: [{ request_id: id, result_key: "conclusion" }] });
    assert.deepEqual(scanned.candidates, []);
    const skipped = await f.run("prepare", legacy);
    assert.equal(skipped.action, "skip");
    assert.equal(skipped.processed, 0);
    await assert.rejects(f.run("attempt", { ...saved, ...legacy, attempt }), /exact delivery profile/);
    assert.deepEqual(await readFile(f.statePath), before);
    const recovery = await f.run("prepare", { now: legacy.now });
    assert.equal(recovery.action, "verify");
    assert.deepEqual(recovery.attempt, attempt);
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

const deadlineCases = [
  {label: "offset before", now: "2026-01-02T04:00:00+01:00", deadline: "2026-01-02T03:30:00+00:00", due: false},
  {label: "offset due", now: "2026-01-02T03:30:00Z", deadline: "2026-01-02T04:00:00+01:00", due: true},
  {label: "microsecond before", now: "2026-01-02T03:30:00Z", deadline: "2026-01-02T03:30:00.000001+00:00", due: false},
  {label: "fraction precision before", now: "2026-01-02T03:30:00.1Z", deadline: "2026-01-02T03:30:00.100001+00:00", due: false},
  {label: "equal instant", now: "2026-01-02T03:30:00+01:00", deadline: "2026-01-02T02:30:00Z", due: true},
  {label: "offset seconds due", now: "2026-01-02T03:30:00.123457Z", deadline: "2026-01-02T04:30:30.123456+01:00:30", due: true},
];

for (const clock of deadlineCases) {
  for (const exact of [false, true]) {
    test(`return retry clock uses instants: ${clock.label} (${exact})`, async () => {
      const f = await fixture(exact, true);
      try {
        await f.put(f.statePath, {status: "retry_pending", retry_at: clock.deadline});
        const bytes = await readFile(f.statePath);
        const scanned = await f.run("scan", {now: clock.now,
          candidates: [{request_id: id, result_key: "conclusion"}]});
        assert.deepEqual(scanned.candidates, clock.due ? [{request_id: id, result_key: "conclusion"}] : []);
        assert.equal(scanned.unavailable_count, 0);
        assert.deepEqual(await readFile(f.statePath), bytes);
        assert.equal((await f.run("prepare", {now: clock.now})).action, clock.due ? "send" : "skip");
        if (!clock.due) assert.deepEqual(await readFile(f.statePath), bytes);
      } finally { await f.close(); }
    });
  }

  test(`return admission clock retains the original attempt: ${clock.label}`, async () => {
    const f = await fixture(true, true);
    try {
      await f.put(f.statePath, {status: "admitted", goal_ref: ref, attempt,
        admission: {token: "prior-send", prior_status: "verification_required", expires_at: clock.deadline}});
      const bytes = await readFile(f.statePath);
      const scanned = await f.run("scan", {now: clock.now,
        candidates: [{request_id: id, result_key: "conclusion"}]});
      assert.deepEqual(scanned.candidates, clock.due ? [{request_id: id, result_key: "conclusion"}] : []);
      assert.deepEqual(await readFile(f.statePath), bytes);
      const plan = await f.run("prepare", {now: clock.now});
      assert.equal(plan.action, clock.due ? "verify" : "skip");
      if (clock.due) {
        assert.deepEqual(plan.attempt, attempt);
        await f.run("settle", {...plan, now: clock.now, outcome: {kind: "verified",
          verification: {verification_performed: true, reply_verified: true}}});
        assert.equal((await f.state()).status, "delivered");
        assert.equal((await f.state()).provider_receipt, attempt.provider_receipt);
      } else {
        assert.deepEqual(await readFile(f.statePath), bytes);
      }
    } finally { await f.close(); }
  });
}

for (const field of ["retry_at", "expires_at"]) {
  for (const malformed of ["not-a-clock", "2026-02-30T03:04:05Z", 42, {}]) {
    test(`return clock damage is unavailable without rewriting ${field}: ${JSON.stringify(malformed)}`, async () => {
      const f = await fixture(true, true);
      try {
        await f.put(f.statePath, field === "retry_at"
          ? {status: "retry_pending", retry_at: malformed}
          : {status: "admitted", admission: {expires_at: malformed}, attempt});
        const bytes = await readFile(f.statePath);
        const scanned = await f.run("scan", {candidates: [{request_id: id, result_key: "conclusion"}]});
        assert.deepEqual(scanned.candidates, []);
        assert.equal(scanned.unavailable_count, 1);
        assert.equal((await f.run("prepare")).action, "skip");
        assert.deepEqual(await readFile(f.statePath), bytes);
      } finally { await f.close(); }
    });
  }
}

test("return clock admission rechecks a due scan after another writer defers it", async () => {
  const f = await fixture(true, true);
  try {
    const candidates = [{request_id: id, result_key: "conclusion"}];
    await f.put(f.statePath, {status: "retry_pending", retry_at: "2026-01-02T03:00:00Z"});
    assert.deepEqual((await f.run("scan", {candidates})).candidates, candidates);
    await f.put(f.statePath, {status: "retry_pending", retry_at: "2026-01-02T04:04:05.123457+01:00"});
    const bytes = await readFile(f.statePath);
    assert.equal((await f.run("prepare")).action, "skip");
    assert.deepEqual(await readFile(f.statePath), bytes);
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

for (const exact of [false, true]) {
  for (const accepted of [false, true]) {
    test(`initial receipt conflict preserves its cause and original return (${exact}, accepted=${accepted})`, async () => {
      const f = await fixture(exact, true);
      try {
        if (accepted) {
          const plan = await f.run("prepare");
          await f.run("attempt", { ...plan, attempt });
        }
        const sourcePaths = [
          f.entryPath, join(f.folder, "roundtrips", id + ".json"),
          join(f.folder, "replies", id, "conclusion.json"),
        ];
        const original = await Promise.all(sourcePaths.map(path => readFile(path)));
        const before = accepted ? await readFile(f.statePath) : null;
        const initial = f.facts.initial_delivery as JsonObject;
        const conflicting = { ...f.facts, initial_delivery: { ...initial,
          turn: { ...(initial.turn as JsonObject), context_handoff_receipt: {
            request_id: "f".repeat(64), goal_id: "research", agent_id: "another-worker",
          } },
        } };
        const result = await f.run("prepare", { facts: conflicting,
          now: "2026-01-02T03:06:05.123456+00:00" });
        assert.equal(result.action, "skip", "a different receiver's receipt cannot authorize a return");
        if (exact) {
          assert.equal(result.source_unavailable, true);
          if (before) assert.deepEqual(await readFile(f.statePath), before);
          else await assert.rejects(readFile(f.statePath));
        } else {
          const held = await f.state();
          assert.equal(held.error, "initial_delivery_receipt_unavailable");
          assert.equal(held.status, accepted ? "explicit_unverified" : "retry_pending");
          if (accepted) {
            assert.deepEqual(held.attempt, attempt, "an accepted write is never reset into a fresh send");
            assert.equal((await f.run("prepare", { now: "2026-01-03T03:06:05Z" })).processed, 0);
            assert.deepEqual(await f.state(), held);
          } else {
            const bytes = await readFile(f.statePath);
            assert.equal((await f.run("prepare", { now: "2026-01-02T03:06:06Z" })).processed, 0);
            assert.deepEqual(await readFile(f.statePath), bytes, "unchanged source honors the original backoff");
            const restored = { ...f.facts, initial_delivery: { ...initial,
              turn: { ...(initial.turn as JsonObject), context_handoff_receipt: initial.request },
            } };
            const plan = await f.run("prepare", { facts: restored, now: "2026-01-02T03:06:16Z" });
            assert.equal(plan.action, "send");
            await f.run("settle", { ...plan, facts: restored, now: "2026-01-02T03:06:16Z",
              outcome: { kind: "sent", reply_verified: true, idempotency_key: attempt.provider_receipt } });
            assert.equal((await f.state()).message_id, plan.message_id);
            assert.equal((await f.run("prepare", { facts: restored, now: "2026-01-03T03:06:16Z" })).processed, 0);
          }
        }
        const retained = await Promise.all(sourcePaths.map(path => readFile(path)));
        assert.deepEqual(retained, original, "diagnosis never rewrites the request, source route or result");
      } finally { await f.close(); }
    });
  }
}
