import test from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {NativeGoalController, type BindingState, type NativeHost} from "../../loopx/zcode_goal_mode/runtime.ts";
import type {NativeObservation, NativeRequest} from "../../loopx/zcode_goal_mode/contract.ts";

function fixture() {
  const request: NativeRequest = {action: "bind", project: "/project", registry: "/registry",
    state_path: "/state", goal_id: "goal_alpha", goal_ref: {goal_id: "goal_alpha", goal_instance_id: "ginst_alpha"},
    agent_id: "zcode", cli_command: ["zcode", "app-server"], loopx_command: ["loopx"],
    validation_command: ["validate"], task_body: "Advance the registered Goal through the LoopX lifecycle"};
  let observation: NativeObservation = {session_id: "session-a", target_id: null, status: null, running: false, selected_model: {providerId: "local", modelId: "test"}};
  let permitted = true;
  let valid = true;
  let quotaFailure = false;
  let journalFailure = false;
  let journalFailureAfterSet = false;
  const calls: string[] = [];
  const journals: BindingState[] = [];
  const host: NativeHost = {
    initialize: async () => {calls.push("initialize");},
    create: async () => {calls.push("create"); return {...observation};},
    resumeSession: async () => {calls.push("restore"); return {...observation};},
    readGoal: async () => ({...observation}),
    setGoal: async (_id, objective) => {calls.push("set"); observation = {...observation, target_id: "target-a", status: "active", running: true, objective_sha256: createHash("sha256").update(objective.trim()).digest("hex")}; return {...observation};},
    pauseGoal: async () => {calls.push("pause"); observation = {...observation, status: "paused", running: false}; return {...observation};},
    resumeGoal: async () => {calls.push("resume"); observation = {...observation, status: "active", running: true}; return {...observation};},
    selectModel: async (_id, selected_model) => {observation = {...observation, selected_model}; return {...observation};},
    clearGoal: async () => {calls.push("clear"); observation = {...observation, target_id: null, status: null, running: false, selected_model: {providerId: "local", modelId: "test"}}; return {...observation};},
    close: async () => {calls.push("close");},
  };
  const deps = {host, validate: async (cleanupOnly = false) => {if (!valid && !cleanupOnly) throw new Error("stale_goal_instance");},
    quota: async () => {if (quotaFailure) throw new Error("quota unavailable"); return {should_run: permitted, reason: permitted ? "work_available" : "goal_stopped", checked_at: "2026-01-01T00:00:00Z"};},
    persist: async (state: BindingState) => {if (journalFailure || (journalFailureAfterSet && calls.includes("set"))) throw new Error("journal unavailable"); journals.push(structuredClone(state));}};
  return {request, deps, calls, journals, observation: () => observation,
    setPermitted: (value: boolean) => {permitted = value;},
    invalidate: () => {valid = false;}, failQuota: () => {quotaFailure = true;},
    failJournal: () => {journalFailure = true;}, failJournalAfterSet: () => {journalFailureAfterSet = true;},
    setObservation: (value: Partial<NativeObservation>) => {observation = {...observation, ...value};},
    complete: () => {observation = {...observation, status: "completed", running: false};}};
}
async function connected() {
  const f = fixture();
  const controller = new NativeGoalController(f.request, null, f.deps);
  await controller.initialize();
  return {...f, controller};
}
const basisA = {acceptance: {revision: 3, contract_digest: "a".repeat(64)}, authority_sources: [{id: "src-1", revision: 2}, {id: "src-0", revision: "r1"}]};
const basisAReordered = {...basisA, authority_sources: [basisA.authority_sources[1], basisA.authority_sources[0]]};
/** A journal written before any basis was recorded: the previous provider shape, same session and target. */
async function legacyJournal() {
  const f = fixture();
  const legacy: BindingState = {schema: "loopx_zcode_native_binding_v0", request: f.request,
    session_id: "session-a", target_id: "target-a", objective_sha256: "a".repeat(64)};
  f.setObservation({target_id: "target-a", status: "paused", running: false, objective_sha256: "a".repeat(64)});
  const controller = new NativeGoalController(f.request, structuredClone(legacy), f.deps);
  await controller.initialize();
  return {...f, controller, legacy};
}
test("binding creates a real host session without starting a native Goal", async () => {
  const f = await connected();
  assert.deepEqual(f.calls, ["initialize", "create"]);
  assert.equal(f.controller.readback().native?.session_id, "session-a");
  assert.deepEqual(f.controller.readback().actions, ["status", "bind", "start"]);
});
test("quota denial starts no native Goal and preserves the decision", async () => {
  const f = await connected(); f.setPermitted(false);
  const result = await f.controller.operate("start");
  assert.equal(result.quota?.should_run, false);
  assert.equal(result.native?.target_id, null);
  assert.equal(f.calls.includes("set"), false);
});
test("pause and resume preserve exact native session and target", async () => {
  const f = await connected();
  const start = await f.controller.operate("start", undefined, undefined, basisA);
  assert.equal(start.native?.running, true);
  const paused = await f.controller.operate("pause");
  assert.equal(paused.native?.status, "paused");
  assert.equal(paused.native?.running, false);
  const resumed = await f.controller.operate("resume", undefined, undefined, basisA);
  assert.equal(resumed.ok, true);
  assert.equal(resumed.native?.running, true);
  assert.equal(resumed.native?.session_id, start.native?.session_id);
  assert.equal(resumed.native?.target_id, start.native?.target_id);
  assert.equal(f.calls.filter(c => c === "set").length, 1);
});
test("concurrent starts are serialized and never replace an existing native Goal", async () => {
  const f = await connected();
  const results = await Promise.all([f.controller.operate("start"), f.controller.operate("start")]);
  assert.equal(results[0].ok, true);
  assert.equal(results[1].ok, false);
  assert.equal(f.calls.filter(c => c === "set").length, 1);
  assert.equal(f.observation().running, true);
});
test("revoked quota pauses an executing target and blocks explicit resume", async () => {
  const f = await connected();
  await f.controller.operate("start", undefined, undefined, basisA); f.setPermitted(false);
  await f.controller.check();
  assert.equal(f.observation().status, "paused");
  assert.equal(f.observation().running, false);
  const refused = await f.controller.operate("resume", undefined, undefined, basisA);
  assert.equal(refused.reason, "goal_stopped", "quota, not the unchanged basis, is what refuses here");
  assert.equal(f.calls.includes("resume"), false);
});
test("quota transport failure fails closed and is not a successful empty decision", async () => {
  const f = await connected();
  await f.controller.operate("start"); f.failQuota();
  await f.controller.check();
  assert.equal(f.controller.readback().quota?.should_run, false);
  assert.equal(f.observation().status, "paused");
});
test("stale authority pauses owned execution and never grants resume", async () => {
  const f = await connected();
  await f.controller.operate("start"); f.invalidate();
  await f.controller.check();
  assert.equal(f.observation().status, "paused");
  const resume = await f.controller.operate("resume");
  assert.equal(resume.ok, false);
  assert.equal(f.calls.includes("resume"), false);
});
test("native completion is an observation and invokes no LoopX completion or credit write", async () => {
  const f = await connected();
  await f.controller.operate("start"); f.complete();
  const result = await f.controller.operate("status");
  assert.equal(result.native?.status, "completed");
  assert.deepEqual(result.actions, ["status", "stop"]);
  assert.deepEqual(f.calls, ["initialize", "create", "set"]);
});
test("stop pauses before clearing and retains the same session for a later goal", async () => {
  const f = await connected();
  await f.controller.operate("start");
  const result = await f.controller.operate("stop");
  assert.deepEqual(f.calls.slice(-2), ["pause", "clear"]);
  assert.equal(result.native?.target_id, null);
  assert.equal(result.native?.session_id, "session-a");
});
test("cold recovery reads and pauses the same target without automatically executing it", async () => {
  const f = await connected();
  await f.controller.operate("start");
  const persisted = f.journals.at(-1)!;
  const recovered = new NativeGoalController(f.request, persisted, f.deps);
  await recovered.initialize();
  assert.equal(recovered.readback().native?.target_id, "target-a");
  assert.equal(recovered.readback().native?.status, "paused");
  assert.equal(f.calls.includes("restore"), true);
  assert.equal(f.calls.includes("resume"), false);
});
test("a different Goal instance cannot reuse the previous provider journal", () => {
  const f = fixture();
  const state: BindingState = {schema: "loopx_zcode_native_binding_v0", request: f.request,
    session_id: "session-a", target_id: "target-a", objective_sha256: "hash"};
  assert.throws(() => new NativeGoalController({...f.request,
    goal_ref: {...f.request.goal_ref, goal_instance_id: "ginst_replacement"}}, state, f.deps), /identity changed/);
  assert.equal(f.calls.length, 0);
});
test("failed persistence after starting pauses the native side effect", async () => {
  const f = await connected(); f.failJournalAfterSet();
  const result = await f.controller.operate("start");
  assert.equal(result.ok, false);
  assert.equal(f.observation().running, false);
  assert.equal(f.observation().status, "paused");
});
test("durability failure before start issues no native set command", async () => {
  const f = await connected(); f.failJournal();
  const result = await f.controller.operate("start");
  assert.equal(result.ok, false);
  assert.equal(f.calls.includes("set"), false);
  assert.equal(f.observation().target_id, null);
});

test("legacy alias binding also fences the immutable creation witness", () => {
  const f = fixture();
  const request = {...f.request, goal_ref: {goal_id: f.request.goal_id}, goal_creation_operation_id: "create-a"};
  const state: BindingState = {schema: "loopx_zcode_native_binding_v0", request,
    session_id: "session-a", target_id: null, objective_sha256: "a".repeat(64)};
  assert.throws(() => new NativeGoalController({...request, goal_creation_operation_id: "create-b"}, state, f.deps), /identity changed/);
});
test("lost set receipt recovers only its durable canonical objective and pauses", async () => {
  const f = await connected();
  const text = "  Canonical updated objective  ";
  await f.controller.operate("start", undefined, text);
  const intent = f.journals.at(-2)!;
  assert.equal(intent.start_pending, true);
  assert.equal(intent.target_id, null);
  assert.equal(intent.request.task_body, text.trim());
  const recovered = new NativeGoalController(f.request, intent, f.deps);
  await recovered.initialize();
  assert.equal(recovered.state.start_pending, false);
  assert.equal(recovered.readback().native?.target_id, "target-a");
  assert.equal(recovered.readback().native?.running, false);
});
test("an unjournaled or different native objective cannot be adopted", async () => {
  const f = await connected();
  const state = structuredClone(f.controller.state);
  state.start_pending = true;
  f.setObservation({target_id: "foreign-target", objective_sha256: "b".repeat(64), status: "paused"});
  const recovered = new NativeGoalController(f.request, state, f.deps);
  await assert.rejects(recovered.initialize(), /unjournaled native Goal/);
  assert.equal(f.calls.includes("set"), false);
  assert.equal(f.calls.includes("clear"), false);
});
test("offline cleanup can restore an inactive binding but cannot execute", async () => {
  const f = await connected();
  await f.controller.operate("start");
  const state = structuredClone(f.controller.state);
  f.invalidate();
  const cleanup = new NativeGoalController(f.request, state, f.deps);
  await cleanup.initialize(true);
  assert.equal((await cleanup.operate("status")).ok, true);
  assert.equal((await cleanup.operate("resume")).ok, false);
  assert.equal((await cleanup.operate("stop")).native?.target_id, null);
  assert.equal(f.calls.includes("resume"), false);
});
test("an unconfigured model blocks start until explicit session model selection", async () => {
  const f = await connected(); f.setObservation({selected_model: null});
  const blocked = await f.controller.operate("start");
  assert.equal(blocked.ok, false);
  assert.equal(f.calls.includes("set"), false);
  const selection = {providerId: "configured", modelId: "explicit"};
  assert.equal((await f.controller.operate("select_model", selection)).native?.selected_model?.modelId, "explicit");
  assert.equal((await f.controller.operate("start")).native?.running, true);
});

test("unknown provider exceptions never expose arbitrary error text", async () => {
  const f = await connected();
  f.deps.host.readGoal = async () => {throw new Error("marker-do-not-echo");};
  const result = await f.controller.operate("status");
  assert.equal(result.ok, false);
  assert.equal(result.reason, "zcode_operation_failed");
  assert.equal(JSON.stringify(result).includes("marker-do-not-echo"), false);
});

test("status refreshes quota, hides denied execution and re-enables it after admission", async () => {
  const f = await connected(); f.setPermitted(false);
  let result = await f.controller.operate("status");
  assert.equal(result.quota?.should_run, false);
  assert.equal(result.actions.includes("start"), false);
  f.setPermitted(true);
  result = await f.controller.operate("status");
  assert.equal(result.quota?.should_run, true);
  assert.equal(result.actions.includes("start"), true);
});

test("a native background model failure is observed, paused and retained", async () => {
  const f = await connected(); await f.controller.operate("start");
  f.setObservation({session_status: "error", running: false});
  await f.controller.check();
  assert.equal(f.observation().status, "paused");
  assert.equal(f.controller.readback().reason, "zcode_native_execution_failed");
  assert.equal(f.journals.at(-1)?.last_execution_error, "zcode_native_execution_failed");
  const status = await f.controller.operate("status");
  assert.equal(status.ok, false);
  assert.equal(status.reason, "zcode_native_execution_failed");
});

test("resume with the same recorded criterion/source basis keeps the native target running", async () => {
  const f = await connected();
  await f.controller.operate("start", undefined, undefined, basisA);
  assert.deepEqual(f.journals.at(-1)?.goal_basis, basisA);
  await f.controller.operate("pause");
  const resumed = await f.controller.operate("resume", undefined, undefined, basisAReordered);
  assert.equal(resumed.ok, true);
  assert.equal(resumed.native?.running, true);
  assert.equal(f.calls.filter(c => c === "resume").length, 1);
});
test("a changed acceptance revision refuses a plain resume and keeps the target paused", async () => {
  const f = await connected();
  await f.controller.operate("start", undefined, undefined, basisA);
  await f.controller.operate("pause");
  const refused = await f.controller.operate("resume", undefined, undefined,
    {...basisA, acceptance: {revision: 4, contract_digest: "b".repeat(64)}});
  assert.equal(refused.ok, false);
  assert.equal(refused.reason, "goal_basis_changed");
  assert.equal(refused.native?.status, "paused");
  assert.equal(refused.native?.target_id, "target-a");
  assert.equal(f.calls.includes("resume"), false);
  assert.deepEqual(f.journals.at(-1)?.goal_basis, basisA, "the recorded basis is not rewritten to follow new intent");
});
test("a changed declared-source revision refuses a plain resume", async () => {
  const f = await connected();
  await f.controller.operate("start", undefined, undefined, basisA);
  await f.controller.operate("pause");
  const refused = await f.controller.operate("resume", undefined, undefined,
    {...basisA, authority_sources: [{id: "src-1", revision: 3}, {id: "src-0", revision: "r1"}]});
  assert.equal(refused.reason, "goal_basis_changed");
  assert.equal(f.calls.includes("resume"), false);
});
test("a resume that cannot present the current basis stays paused", async () => {
  const f = await connected();
  await f.controller.operate("start", undefined, undefined, basisA);
  await f.controller.operate("pause");
  const refused = await f.controller.operate("resume");
  assert.equal(refused.ok, false);
  assert.equal(refused.reason, "goal_basis_unavailable");
  assert.equal(f.calls.includes("resume"), false);
});
test("stop then start consumes the current basis through a fresh native objective", async () => {
  const f = await connected();
  await f.controller.operate("start", undefined, undefined, basisA);
  await f.controller.operate("pause");
  const current = {...basisA, acceptance: {revision: 4, contract_digest: "b".repeat(64)}};
  assert.equal((await f.controller.operate("resume", undefined, undefined, current)).reason, "goal_basis_changed");
  const stopped = await f.controller.operate("stop");
  assert.equal(stopped.native?.target_id, null);
  assert.equal(f.journals.at(-1)?.goal_basis, undefined);
  const started = await f.controller.operate("start", undefined, undefined, current);
  assert.equal(started.native?.running, true);
  assert.deepEqual(f.journals.at(-1)?.goal_basis, current);
  assert.equal(f.calls.filter(c => c === "set").length, 2);
});
test("a journal recorded before any basis cannot prove the current one and keeps its target paused", async () => {
  const f = await legacyJournal();
  const journalsAfterRecovery = f.journals.length;
  for (const presented of [basisA, undefined]) {
    const refused = await f.controller.operate("resume", undefined, undefined, presented);
    assert.equal(refused.ok, false);
    assert.equal(refused.reason, "goal_basis_unavailable");
    assert.equal(refused.native?.target_id, "target-a");
    assert.equal(refused.native?.status, "paused");
  }
  assert.equal(f.calls.includes("resume"), false);
  assert.equal(f.journals.length, journalsAfterRecovery, "a refused resume writes nothing");
  assert.equal(f.journals.at(-1)?.goal_basis, undefined, "resume never backfills a basis it did not deliver");
});
test("a legacy journal file is read back byte for byte and left unchanged by a refused resume", async () => {
  const {mkdtemp, readFile, rm} = await import("node:fs/promises");
  const {join} = await import("node:path");
  const {tmpdir} = await import("node:os");
  const {atomicState, readState} = await import("../../loopx/zcode_goal_mode/runtime.ts");
  const dir = await mkdtemp(join(tmpdir(), "zcode-legacy-"));
  try {
    const path = join(dir, "binding.json");
    const f = fixture();
    const legacy: BindingState = {schema: "loopx_zcode_native_binding_v0", request: f.request,
      session_id: "session-a", target_id: "target-a", objective_sha256: "a".repeat(64)};
    await atomicState(path, legacy);
    const bytes = await readFile(path);
    f.setObservation({target_id: "target-a", status: "paused", running: false, objective_sha256: "a".repeat(64)});
    const controller = new NativeGoalController(f.request, await readState(path), {...f.deps, persist: value => atomicState(path, value)});
    await controller.initialize();
    const refused = await controller.operate("resume", undefined, undefined, basisA);
    assert.equal(refused.reason, "goal_basis_unavailable");
    assert.deepEqual(await readFile(path), bytes);
    assert.equal((await readState(path))?.goal_basis, undefined);
  } finally { await rm(dir, {recursive: true, force: true}); }
});
test("a legacy journal stays readable and stoppable; stop then start records the basis it delivers", async () => {
  const f = await legacyJournal();
  const status = await f.controller.operate("status");
  assert.equal(status.ok, true);
  assert.equal(status.reason, undefined);
  assert.deepEqual(status.actions, ["status", "stop", "resume"]);
  assert.equal((await f.controller.operate("pause")).reason, "user_paused");
  const stopped = await f.controller.operate("stop");
  assert.equal(stopped.ok, true);
  assert.equal(stopped.native?.target_id, null);
  assert.equal(f.journals.at(-1)?.goal_basis, undefined);
  const started = await f.controller.operate("start", undefined, undefined, basisA);
  assert.equal(started.native?.running, true);
  assert.deepEqual(f.journals.at(-1)?.goal_basis, basisA, "start records exactly the basis it delivered");
  await f.controller.operate("pause");
  const resumed = await f.controller.operate("resume", undefined, undefined, basisAReordered);
  assert.equal(resumed.ok, true);
  assert.equal(resumed.native?.running, true);
  await f.controller.operate("pause");
  const changed = await f.controller.operate("resume", undefined, undefined, {...basisA, acceptance: {revision: 4, contract_digest: "b".repeat(64)}});
  assert.equal(changed.reason, "goal_basis_changed");
  assert.equal(f.calls.filter(c => c === "resume").length, 1);
});
test("a malformed recorded basis is an unsupported binding", async () => {
  const f = await connected();
  const state = structuredClone(f.controller.state);
  (state as unknown as {goal_basis: unknown}).goal_basis = {acceptance: {revision: "3", contract_digest: null}, authority_sources: []};
  const {writeFile, mkdtemp, rm} = await import("node:fs/promises");
  const {join} = await import("node:path");
  const {tmpdir} = await import("node:os");
  const {readState} = await import("../../loopx/zcode_goal_mode/runtime.ts");
  const dir = await mkdtemp(join(tmpdir(), "zcode-basis-"));
  try {
    const path = join(dir, "binding.json");
    await writeFile(path, JSON.stringify(state));
    await assert.rejects(readState(path), /Unsupported ZCode binding/);
    await writeFile(path, JSON.stringify({...state, goal_basis: basisA}));
    assert.deepEqual((await readState(path))?.goal_basis, basisA);
  } finally { await rm(dir, {recursive: true, force: true}); }
});
