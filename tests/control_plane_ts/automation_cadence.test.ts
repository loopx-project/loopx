import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, mkdir, rm, readFile, writeFile, readdir} from "node:fs/promises";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import {execFileSync} from "node:child_process";
import {admitAutomationStart as admit, cadenceStorePath, confirmAutomationStart as confirm, manageAutomationCadence as manage, projectCadenceProgression as progression, requireAutomationCadenceEligibility} from "../../loopx/control_plane/quota/automation_cadence.ts";

test("owner projects lane-safe eligibility without changing legacy aliases", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-readback-"));
  const originalNow = Date.now;
  const scope = {runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: null};
  try {
    Date.now = () => 1000;
    const unconfigured = await manage({...scope, operation: "read"});
    assert.deepEqual(unconfigured.eligibility, {
      state: "unconfigured", reason: "unconfigured",
      eligible_now: null, next_eligible_at_ms: null,
    });
    assert.equal(unconfigured.eligible_now, true);

    await manage({...scope, agent_id: null, operation: "configure", expected_revision: 0,
      min_interval_minutes: 60, owner_reference: "owner-request", execute: true});
    const goal = await manage({...scope, agent_id: null, operation: "read"});
    assert.deepEqual(goal.eligibility, {
      state: "unavailable", reason: "agent_scope_required",
      eligible_now: null, next_eligible_at_ms: null,
    });
    assert.equal(goal.eligible_now, true);

    const ready = await manage({...scope, operation: "read"});
    assert.deepEqual(ready.eligibility, {
      state: "eligible", reason: "owner_minimum_interval",
      eligible_now: true, next_eligible_at_ms: null,
    });

    await admit({...scope, request_id: "first", now_ms: 1000, trigger_at_ms: 1000});
    Date.now = () => 1000 + 3_600_000 - 1;
    const waiting = await manage({...scope, operation: "read"});
    assert.deepEqual(waiting.eligibility, {
      state: "waiting", reason: "minimum_interval_wait",
      eligible_now: false, next_eligible_at_ms: 1000 + 3_600_000,
    });
    Date.now = () => 1000 + 3_600_000;
    assert.deepEqual((await manage({...scope, operation: "read"})).eligibility, {
      state: "eligible", reason: "owner_minimum_interval",
      eligible_now: true, next_eligible_at_ms: 1000 + 3_600_000,
    });
  } finally {
    Date.now = originalNow;
    await rm(root, {recursive: true, force: true});
  }
});

test("eligibility decoder rejects impossible public states", () => {
  assert.deepEqual(requireAutomationCadenceEligibility({
    state: "waiting", reason: "minimum_interval_wait",
    eligible_now: false, next_eligible_at_ms: 42,
  }), {
    state: "waiting", reason: "minimum_interval_wait",
    eligible_now: false, next_eligible_at_ms: 42,
  });
  assert.throws(() => requireAutomationCadenceEligibility({
    state: "waiting", reason: "minimum_interval_wait",
    eligible_now: false, next_eligible_at_ms: null,
  }), /next eligible/);
  assert.throws(() => requireAutomationCadenceEligibility({
    state: "eligible", reason: "owner_minimum_interval",
    eligible_now: false, next_eligible_at_ms: null,
  }), /eligible now/);
});

test("owner floor inherits without changing another agent; reductions and concurrent writes require authority", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-"));
  const base = {runtime_root: root, goal_id: "fixture", operation: "configure", execute: true, owner_reference: "owner-request"};
  const read = (extra = {}) => manage({...base, operation: "read", ...extra});
  try {
    assert.equal((await read()).enabled, false);
    assert.deepEqual(await readdir(root), []);
    const preview = await manage({...base, expected_revision: 0, min_interval_minutes: 60, execute: false});
    assert.equal(preview.preview, true);
    assert.deepEqual(await readdir(root), []);
    await manage({...base, expected_revision: 0, min_interval_minutes: 60});
    await manage({...base, agent_id: "a", expected_revision: 1, min_interval_minutes: 1440});
    await manage({...base, agent_id: "a", automation_id: "daily", expected_revision: 2, min_interval_minutes: 2880});
    assert.equal((await read({agent_id: "a", automation_id: "daily"})).min_interval_minutes, 2880);
    assert.equal((await read({agent_id: "a", automation_id: "other"})).min_interval_minutes, 1440);
    assert.equal((await read({agent_id: "b"})).min_interval_minutes, 60);
    await assert.rejects(manage({...base, agent_id: "a", expected_revision: 3, min_interval_minutes: 1}), /owner-approved/);
    // Stale configuration intent is a typed conflict, so callers do not have to
    // parse the message to tell "refresh and retry" from an invalid request.
    const staleRevision = await manage({...base, expected_revision: 0, min_interval_minutes: 100})
      .then(() => null, (error: {kind?: string; code?: string; message?: string}) => error);
    assert.equal(staleRevision?.kind, "conflict");
    assert.equal(staleRevision?.code, "automation_cadence_revision_conflict");
    assert.match(String(staleRevision?.message), /revision conflict/);
    const concurrent = await Promise.allSettled([120, 180].map(min => manage({...base, expected_revision: 3, min_interval_minutes: min})));
    assert.equal(concurrent.filter(r => r.status === "fulfilled").length, 1);
    assert.equal(concurrent.filter(r => r.status === "rejected").length, 1);
    const cleared = await manage({...base, agent_id: "a", expected_revision: 4, min_interval_minutes: 0, approve_reduction: true});
    assert.equal(cleared.configuration_revision, 5);
    assert.ok([120, 180].includes(Number(cleared.min_interval_minutes)));
    assert.equal((await read({agent_id: "a", automation_id: "daily"})).min_interval_minutes, 2880);
    // A fresh read reconstructs the policy from the real file, not process cache.
    const saved = JSON.parse(await readFile(cadenceStorePath(root, "fixture"), "utf8"));
    assert.equal(saved.revision, 5);
    assert.equal(saved.rules.length, 3);
    assert.equal((await read()).enforcement, "managed_turn_atomic_admission_and_schedule_recommendation");
    await writeFile(cadenceStorePath(root, "fixture"), '{"broken":true}');
    await assert.rejects(read(), /identity\/schema mismatch/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("managed starts are atomic, durable, per agent, and due at the exact interval", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-admit-"));
  const base = {runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: "daily"};
  const start = (request_id: string, now_ms: number, extra = {}) => admit({...base, request_id,
    now_ms, trigger_at_ms: now_ms, ...extra});
  try {
    assert.equal((await start("unconfigured", 1000)).reserved, false);
    assert.deepEqual(await readdir(root), []);
    await manage({...base, operation: "configure", expected_revision: 0, min_interval_minutes: 1440,
      owner_reference: "owner-request", execute: true, agent_id: null, automation_id: null});
    const concurrent = await Promise.all([start("one", 1000), start("two", 1000)]);
    assert.equal(concurrent.filter(r => r.admitted === true).length, 1);
    assert.equal(concurrent.filter(r => r.admitted === false).length, 1);
    assert.equal((await start("early", 1000 + 86_400_000 - 1)).reason, "minimum_interval_wait");
    assert.equal((await start("due", 1000 + 86_400_000)).admitted, true);
    // An unconfirmed reservation resumes the same identity; a confirmed start is fail-closed.
    assert.equal((await start("due", 1000 + 2 * 86_400_000)).reason, "resumed_unstarted_reservation");
    await confirm({...base, request_id: "due"});
    assert.equal((await start("due", 1000 + 3 * 86_400_000)).reason, "duplicate_or_stale_trigger");
    assert.equal((await start("stale", 1000 + 86_400_000, {trigger_at_ms: 999})).admitted, false);
    assert.equal((await admit({...base, agent_id: "b", request_id: "other-agent", now_ms: 1001, trigger_at_ms: 1001})).admitted, true);
    const manual = await start("manual", 1000 + 86_400_001, {manual_reason: "owner-request"});
    assert.equal(manual.reason, "explicit_manual_interval_bypass");
    assert.equal((await start("after-manual", 1000 + 2 * 86_400_000)).admitted, false);
    const saved = JSON.parse(await readFile(cadenceStorePath(root, "fixture"), "utf8"));
    assert.equal(saved.starts.length, 2); // one durable Goal-floor start per agent
    assert.equal(saved.starts.find((row: {agent_id: string}) => row.agent_id === "a").manual_reason, "owner-request");
    await writeFile(cadenceStorePath(root, "fixture"), '{"broken":true}');
    await assert.rejects(start("corrupt", 1000 + 3 * 86_400_000), /identity\/schema mismatch/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("automation-specific floors do not serialize unrelated automation lanes", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-scopes-"));
  try {
    for (const [automation_id, expected_revision] of [["first", 0], ["second", 1]] as const) {
      await manage({runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id,
        operation: "configure", expected_revision, min_interval_minutes: 60,
        owner_reference: "owner-request", execute: true});
    }
    const start = (automation_id: string, request_id: string, now_ms: number) => admit({
      runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id,
      request_id, now_ms, trigger_at_ms: now_ms,
    });
    assert.equal((await start("first", "one", 1000)).admitted, true);
    assert.equal((await start("second", "two", 1001)).admitted, true);
    assert.equal((await start("first", "three", 1002)).reason, "minimum_interval_wait");
    assert.equal((await start("second", "four", 1003)).reason, "minimum_interval_wait");
    const saved = JSON.parse(await readFile(cadenceStorePath(root, "fixture"), "utf8"));
    assert.equal(saved.starts.length, 3); // one agent-wide record and two scoped records
  } finally {await rm(root, {recursive: true, force: true});}
});

test("a restarted process observes the durable start before admitting another", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-restart-"));
  try {
    await manage({runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: null,
      operation: "configure", expected_revision: 0, min_interval_minutes: 60,
      owner_reference: "owner-request", execute: true});
    await admit({runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: null,
      request_id: "first", now_ms: 1000, trigger_at_ms: 1000});
    const moduleUrl = new URL("../../loopx/control_plane/quota/automation_cadence.ts", import.meta.url).href;
    const inChild = (request_id: string, now_ms: number) => JSON.parse(execFileSync(process.execPath,
      ["--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
        `import {admitAutomationStart} from ${JSON.stringify(moduleUrl)}; console.log(JSON.stringify(await admitAutomationStart(${JSON.stringify({runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: null, request_id, now_ms, trigger_at_ms: now_ms})})));`],
      {encoding: "utf8"}));
    assert.equal(inChild("early", 1000 + 3_600_000 - 1).admitted, false);
    assert.equal(inChild("due", 1000 + 3_600_000).admitted, true);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("an unconfirmed reservation resumes while a confirmed start fails closed", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-resume-"));
  const base = {runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: null};
  const start = (request_id: string, now_ms: number, extra = {}) => admit({...base, request_id,
    now_ms, trigger_at_ms: now_ms, ...extra});
  const path = cadenceStorePath(root, "fixture");
  try {
    assert.equal((await confirm({...base, request_id: "absent"})).reason, "reservation_missing");
    assert.deepEqual(await readdir(root), []); // neither phase creates files with no policy
    await manage({...base, operation: "configure", expected_revision: 0, min_interval_minutes: 60,
      owner_reference: "owner-request", execute: true});
    assert.equal((await start("turn:1", 1000)).reserved, true);
    assert.equal((await start("turn:2", 1001)).reason, "minimum_interval_wait");
    const resumed = await start("turn:1", 1001, {trigger_at_ms: 1000});
    assert.equal(resumed.admitted, true);
    assert.equal(resumed.resumed, true);
    assert.equal(resumed.reason, "resumed_unstarted_reservation");
    const reserved = JSON.parse(await readFile(path, "utf8")).starts[0];
    assert.equal(reserved.state, "reserved");
    assert.equal(reserved.started_at_ms, 1000); // the owner floor anchor never moves
    assert.equal((await confirm({...base, request_id: "turn:1"})).reason, "start_confirmed");
    assert.equal((await confirm({...base, request_id: "turn:1"})).reason, "already_confirmed");
    assert.equal(JSON.parse(await readFile(path, "utf8")).starts[0].state, "started");
    // A confirmed host attempt is fail-closed for the same identity, bypass or not.
    assert.equal((await start("turn:1", 1000 + 2 * 3_600_000)).reason, "duplicate_or_stale_trigger");
    assert.equal((await start("turn:1", 1000 + 2 * 3_600_000,
      {manual_reason: "owner-request"})).reason, "duplicate_or_stale_trigger");
    // A record written without the phase field is read as an attempted start.
    const store = JSON.parse(await readFile(path, "utf8"));
    delete store.starts[0].state;
    await writeFile(path, JSON.stringify(store));
    assert.equal((await start("turn:1", 1000 + 3 * 3_600_000)).reason, "duplicate_or_stale_trigger");
  } finally {await rm(root, {recursive: true, force: true});}
});

test("an M1 policy file upgrades in place without losing its configured floor", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-migration-"));
  try {
    const path = cadenceStorePath(root, "fixture");
    await mkdir(dirname(path), {recursive: true});
    await writeFile(path, JSON.stringify({schema_version: "automation_cadence_store_v1",
      goal_id: "fixture", revision: 1, rules: [{agent_id: "a", automation_id: null,
        min_interval_minutes: 60, revision: 1, owner_reference: "owner-request"}]}));
    const input = {runtime_root: root, goal_id: "fixture", agent_id: "a", automation_id: null};
    assert.equal((await manage({...input, operation: "read"})).min_interval_minutes, 60);
    assert.equal((await admit({...input, request_id: "first", now_ms: 1000, trigger_at_ms: 1000})).reserved, true);
    const saved = JSON.parse(await readFile(path, "utf8"));
    assert.equal(saved.schema_version, "automation_cadence_store_v2");
    assert.equal(saved.rules[0].min_interval_minutes, 60);
    assert.equal(saved.starts.length, 1);
    assert.equal(saved.starts[0].state, "reserved");
  } finally {await rm(root, {recursive: true, force: true});}
});

test("every backoff/reset interval respects the owner floor without a 60-minute ceiling", () => {
  for (const sequence of [[3, 6, 12], [15, 30, 60], [120, 240, 480], [1440, 2880]]) {
    const result = progression({progression: sequence, min_interval_minutes: 1440});
    assert.ok((result.progression as number[]).every(n => n >= 1440));
  }
  assert.deepEqual(progression({progression: [3, 6, 12], min_interval_minutes: 0}).progression, [3, 6, 12]);
  assert.throws(() => progression({progression: [1], min_interval_minutes: -1}));
  assert.throws(() => progression({progression: [1], min_interval_minutes: 1.5}));
});
