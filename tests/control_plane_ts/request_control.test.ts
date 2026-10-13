import assert from "node:assert/strict";
import test from "node:test";
import {planRequestControl, planControlledRequestPublication, requestControlView} from "../../loopx/control_plane/collaboration/request_control.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const brief = {schema_version: "collaboration_brief_v0", purpose: "Validate the draft", context: "Keep the original constraints",
  constraints: ["Do not publish"], inputs: [], acceptance: ["Independent check passes"], return_requirement: "Return findings"};
const ref = {goal_id: "request-goal", goal_instance_id: "ginst_" + "a".repeat(32)};
const original = {schema_version: "loopx_manager_context_entry_v2", request_id: "a".repeat(64), goal_id: ref.goal_id,
  goal_ref: ref, agent_id: "receiver", source_kind: "peer", source_agent_id: "requester", source_id: "peer:original",
  parent_request_id: null, inherited_context: null, message: brief.purpose, brief, instruction: "Original guide"};
const at = "2026-01-01T00:00:00Z";
const legacy = {request_id: original.request_id, goal_id: ref.goal_id, goal_ref: ref, agent_id: original.agent_id,
  decision: "adopt", reason: "Check this request", decided_at: at};
const amendment = {authority_source_sha256: "b".repeat(64), proposal_id: "proposal-1", proposal_digest: "c".repeat(64),
  base_goal_revision: 1, base_intent_digest: "d".repeat(64), base_work_digest: "e".repeat(64), operation_id: "commit-1"};
function fixture() {
  let entry: JsonObject = {...original, delivered_at: at};
  const view = () => requestControlView(entry, legacy);
  const basis = () => ({expected_revision: view().revision, expected_digest: view().revision_digest});
  const run = (operation: string, params: JsonObject, actor = "source", extras: JsonObject = {}) => {
    const result = planRequestControl({entry, input: {operation, ...params}, source: {
      actor, actor_agent_id: actor === "receiver" ? "receiver" : "requester", observed_at: at,
      goal_active: true, legacy_assessment: legacy, amendment, ...extras}});
    if (result.changed) entry = result.entry as JsonObject;
    return result;
  };
  const reserve = () => run("reserve", {operation_id: "reserve-1", ...basis()}, "receiver");
  const terminal = (outcome: "committed" | "aborted" | "rejected") => {
    const active = view().active_reservation as JsonObject;
    return {operation_id: "commit-1", reservation_digest: active.digest, outcome, goal_ref: ref,
      goal_changed: outcome === "committed", goal_revision: outcome === "committed" ? 2 : null,
      todo_id: outcome === "committed" ? "todo-created" : null, receipt_sha256: "f".repeat(64)};
  };
  return {view, basis, run, reserve, terminal, entry: () => entry};
}

test("request correction preserves original identity and does not inherit old assessment", () => {
  const f = fixture(), old = f.basis();
  assert.equal(f.entry().request_control, undefined);
  f.run("revise", {operation_id: "correction-1", ...old, brief: {...brief, context: "Use the corrected input"}, reason: "Input changed"});
  assert.equal(f.view().revision, 2); assert.equal(f.view().assessment, null);
  assert.deepEqual(f.entry().brief, brief);
  assert.throws(() => f.run("reserve", {operation_id: "reserve-old", ...old}, "receiver"), /basis changed/);
  assert.throws(f.reserve, /adopt this exact/);
  const projected = planControlledRequestPublication({request: {...original, instruction: "New generated guide"}, operation: original, entry: f.entry()});
  assert.equal(projected.replayed, true); assert.deepEqual(projected.entry, f.entry());
  assert.throws(() => planControlledRequestPublication({request: {...original, brief: {...brief, purpose: "Other task"}}, operation: original, entry: f.entry()}), /identity conflict/);
});

test("cancellation before reservation prevents any new effect", () => {
  const f = fixture(), basis = f.basis();
  const input = {operation_id: "cancel-1", ...basis, reason: "Stop this request"};
  f.run("cancel", input);
  assert.equal(f.view().control_state, "cancelled");
  assert.throws(f.reserve, /cannot reserve/);
  const before = structuredClone(f.entry());
  assert.equal(f.run("cancel", input).replayed, true);
  assert.deepEqual(f.entry(), before);
  assert.throws(() => f.run("cancel", {...input, reason: "Different cancellation"}), /identity conflict/);
});

for (const outcome of ["committed", "aborted", "rejected"] as const) {
  test(`pending cancellation only settles from the exact ${outcome} Goal receipt`, () => {
    const f = fixture(); f.reserve();
    f.run("cancel", {operation_id: "cancel-1", ...f.basis(), reason: "Stop this request"});
    assert.equal(f.view().control_state, "cancellation_pending");
    const terminal = f.terminal(outcome);
    assert.throws(() => f.run("settle", {operation_id: "link-1", reservation_digest: terminal.reservation_digest}, "receiver",
      {terminal: {...terminal, operation_id: "another-commit"}}), /does not match/);
    f.run("settle", {operation_id: "link-1", reservation_digest: terminal.reservation_digest}, "receiver", {terminal});
    assert.equal(f.view().control_state, "cancelled");
    assert.equal(f.view().active_reservation, null);
    assert.deepEqual((f.view().terminal_effects as JsonObject[])[0].terminal, terminal);
    // The original reservation receipt stays readable after cancellation;
    // its historical replay is not a fresh effect or a reopened fence.
    assert.equal(f.reserve().replayed, true);
    assert.equal(f.view().active_reservation, null);
    assert.throws(() => f.run("reserve", {operation_id: "reserve-new", ...f.basis()}, "receiver",
      {amendment: {...amendment, operation_id: "commit-new"}}), /cannot reserve/);
  });
}

test("correction during an effect preserves its old revision until receipt linkage", () => {
  const f = fixture(); f.reserve(); const reserved = structuredClone(f.view().active_reservation);
  f.run("revise", {operation_id: "correct-2", ...f.basis(), brief: {...brief, purpose: "Check revised draft"}, reason: "Draft changed"});
  assert.equal(f.view().revision, 2); assert.deepEqual(f.view().active_reservation, reserved);
  const current = f.view();
  f.run("assess", {operation_id: "assess-2", revision: current.revision, revision_digest: current.revision_digest,
    decision: "adopt", reason: "Use the revised source"}, "receiver");
  assert.throws(() => f.run("reserve", {operation_id: "reserve-2", ...f.basis()}, "receiver",
    {amendment: {...amendment, operation_id: "commit-2", proposal_id: "proposal-2"}}), /cannot reserve/);
  const terminal = f.terminal("aborted");
  f.run("settle", {operation_id: "link-1", reservation_digest: terminal.reservation_digest}, "receiver", {terminal});
  const result = f.run("reserve", {operation_id: "reserve-2", ...f.basis()}, "receiver",
    {amendment: {...amendment, operation_id: "commit-2", proposal_id: "proposal-2"}});
  const bound = ((result.result as JsonObject).reservation as JsonObject).binding as JsonObject;
  assert.equal(bound.attempt_epoch, 2); assert.equal(bound.request_revision, 2);
});

test("read evidence stays with the supplied revision; a receiver cannot correct source intent", () => {
  const f = fixture(), initial = f.view();
  assert.throws(() => f.run("revise", {operation_id: "forged-change", ...f.basis(), brief, reason: "Change"}, "receiver"), /authenticated source/);
  f.run("revise", {operation_id: "source-change", ...f.basis(), brief: {...brief, context: "Corrected"}, reason: "Source correction"});
  f.run("read", {operation_id: "read-old", revision: initial.revision, revision_digest: initial.revision_digest}, "receiver");
  assert.equal(f.view().read_at, null);
  assert.throws(() => f.run("assess", {operation_id: "assess-old", revision: initial.revision,
    revision_digest: initial.revision_digest, decision: "adopt", reason: "Old source"}, "receiver"), /stale/);
});

test("every admitted large request effect keeps space for cancellation and terminal linkage", () => {
  let admitted = 0;
  for (const characters of [28000, 30000, 31000, 32000]) {
    let entry: JsonObject = {...original, message: "界".repeat(characters),
      brief: {...brief, context: "x".repeat(5500)}, delivered_at: at};
    const run = (operation: string, fields: JsonObject, actor: string, extra: JsonObject = {}) => {
      const result = planRequestControl({entry, input: {operation, ...fields}, source: {actor,
        actor_agent_id: actor === "source" ? "requester" : "receiver", goal_active: true,
        observed_at: at, legacy_assessment: legacy, amendment, ...extra}});
      if (result.changed) entry = result.entry as JsonObject;
      return result;
    };
    let view = requestControlView(entry, legacy);
    try {
      run("revise", {operation_id: "large-revision", expected_revision: view.revision, expected_digest: view.revision_digest,
        brief: {...brief, context: "y".repeat(5500)}, reason: "r".repeat(2000)}, "source");
      view = requestControlView(entry, legacy);
      run("assess", {operation_id: "large-assessment", revision: view.revision, revision_digest: view.revision_digest,
        decision: "adopt", reason: "Read the revision"}, "receiver");
      run("reserve", {operation_id: "large-reserve", expected_revision: view.revision, expected_digest: view.revision_digest}, "receiver");
    } catch (error) {
      assert.match(String(error), /capacity/); continue;
    }
    admitted++;
    view = requestControlView(entry, legacy);
    const reservation = view.active_reservation as JsonObject;
    run("cancel", {operation_id: "c".repeat(160), expected_revision: view.revision, expected_digest: view.revision_digest,
      reason: "界".repeat(2000)}, "source");
    const terminal = {operation_id: "commit-1", reservation_digest: reservation.digest, outcome: "aborted", goal_ref: ref,
      goal_changed: false, goal_revision: null, todo_id: null, receipt_sha256: "f".repeat(64)};
    run("settle", {operation_id: "s".repeat(160), reservation_digest: reservation.digest}, "receiver", {terminal});
    assert.equal(requestControlView(entry).control_state, "cancelled");
    assert.ok(Buffer.byteLength(JSON.stringify(entry), "utf8") <= 128000);
  }
  assert.ok(admitted > 0, "The workload must exercise an admitted reservation");
});
