import assert from "node:assert/strict";
import test from "node:test";
import {createHash} from "node:crypto";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {planRequestControl, requestControlView} from "../../loopx/control_plane/collaboration/request_control.ts";
import {planCollaborationResult} from "../../loopx/control_plane/collaboration/result_publication.ts";
import { inspectCollaborationInboxReceipts, projectReceiverFollowthrough } from "../../loopx/control_plane/collaboration/inbox_receipts.ts";

const identity = { request_id: "a".repeat(64), goal_id: "delivery", agent_id: "receiver", source_id: "peer:request" };
const decision = { request_id: identity.request_id, goal_id: identity.goal_id, agent_id: identity.agent_id, decision: "adopt" };
const conclusion = { ...identity, decision: "adopt", phase: "conclusion", text: "Draft ready; not published." };
const absent = { state: "absent" };
const read = (value: unknown) => ({ state: "read", value });
const state = (patch: object = {}) => inspectCollaborationInboxReceipts({ observations: [{
  request: identity, route_present: true, decision: absent, conclusion: absent, ...patch,
}] }).items;

test("an ACK is distinct from an owed conclusion and accepted work", () => {
  assert.deepEqual(state(), [{ kind: "pending", recorded_decision: null, warnings: [] }]);
  assert.deepEqual(state({ decision: read(decision) }), [{ kind: "awaiting_conclusion", recorded_decision: "adopt", warnings: [] }]);
  assert.deepEqual(state({ decision: read(decision), conclusion: read(conclusion) }), [{ kind: "settled", recorded_decision: "adopt", warnings: [] }]);
  assert.deepEqual(state({ decision: read(decision), route_present: false }), [{ kind: "settled", recorded_decision: "adopt", warnings: [] }]);
  for (const disposition of ["defer", "reject", "no_change"]) {
    assert.deepEqual(state({ decision: read({ ...decision, decision: disposition }) }), [
      { kind: "awaiting_conclusion", recorded_decision: disposition, warnings: [] },
    ]);
  }
});

test("unavailable, empty and conflicting receipts never clear an owed request", () => {
  for (const result of [read([]), read({ ...conclusion, text: " " }),
    read({ ...conclusion, source_id: "another" }), read({ ...conclusion, phase: "decision" }),
    read({ ...conclusion, decision: "reject" }), { state: "unavailable" }]) {
    assert.deepEqual(state({ decision: read(decision), conclusion: result }), [
      { kind: "receipt_unavailable", recorded_decision: "adopt", warnings: ["conclusion_unreadable_or_conflicting"] },
    ]);
  }
  assert.deepEqual(state({ decision: { state: "unavailable" }, conclusion: read(conclusion) }), [
    { kind: "receipt_unavailable", recorded_decision: null, warnings: ["decision_unreadable_or_conflicting"] },
  ]);
  assert.deepEqual(state({ conclusion: read(conclusion) }), [
    { kind: "receipt_unavailable", recorded_decision: null, warnings: ["decision_missing_for_conclusion"] },
  ]);
});

test("receipt identity includes the exact Goal instance, independent of object order", () => {
  const goal_ref = { goal_id: "delivery", goal_instance_id: "ginst_" + "b".repeat(32) };
  const request = { ...identity, goal_ref };
  const adopted = read({ ...decision, goal_ref: { goal_instance_id: goal_ref.goal_instance_id, goal_id: goal_ref.goal_id } });
  const items = inspectCollaborationInboxReceipts({ observations: [
    { request, route_present: true, decision: adopted, conclusion: read({ ...conclusion, goal_ref }) },
    { request, route_present: true, decision: adopted, conclusion: read({ ...conclusion, goal_ref: { ...goal_ref, goal_instance_id: "ginst_" + "c".repeat(32) } }) },
  ] }).items;
  assert.deepEqual(items, [
    { kind: "settled", recorded_decision: "adopt", warnings: [] },
    { kind: "receipt_unavailable", recorded_decision: "adopt", warnings: ["conclusion_unreadable_or_conflicting"] },
  ]);
});

test("batches remain bounded and do not truncate Unicode reply limits", () => {
  assert.throws(() => inspectCollaborationInboxReceipts({ observations: Array(129).fill({}) }), /at most 128/);
  assert.deepEqual(state({ decision: read(decision), conclusion: read({ ...conclusion, text: "😀".repeat(20000) }) }), [
    { kind: "settled", recorded_decision: "adopt", warnings: [] },
  ]);
  assert.deepEqual(state({ decision: read(decision), conclusion: read({ ...conclusion, text: "😀".repeat(20001) }) }), [
    { kind: "receipt_unavailable", recorded_decision: "adopt", warnings: ["conclusion_unreadable_or_conflicting"] },
  ]);
});

test("receiver advice keeps assessment, linked work and return distinct", () => {
  const project = (patch: object = {}) => projectReceiverFollowthrough({ observations: [{
    kind: "pending", recorded_decision: null, linked_todos: [], evidence_refs: [], evidence_unavailable: false, ...patch,
  }] }).items as Array<Record<string, unknown>>;
  assert.equal(project()[0].step, "assess_request");
  assert.equal(project()[0].assessment_required, true);
  // Adoption can be answered directly; it does not require creating a Todo.
  assert.equal(project({ recorded_decision: "adopt", kind: "awaiting_conclusion" })[0].step, "review_request_work");
  for (const status of ["open", "blocked", "deferred"]) {
    assert.equal(project({ recorded_decision: "adopt", kind: "awaiting_conclusion",
      linked_todos: [{ todo_id: "todo_delivery", status }] })[0].step, "review_request_work");
  }
  const done = project({ recorded_decision: "adopt", kind: "awaiting_conclusion",
    linked_todos: [{ todo_id: "todo_delivery", status: "done" }] })[0];
  assert.equal(done.step, "review_request_work");
  assert.equal(done.answer_owed, true);
  assert.equal(done.request_completion, "not_established_by_receipts_or_todo_status");
  assert.equal(project({kind: "settled", recorded_decision: "adopt",
    linked_todos: [{todo_id: "todo_delivery", status: "open"}]})[0].step, "inspect_recorded_result");
  for (const decision of ["defer", "reject", "no_change"]) {
    assert.equal(project({ recorded_decision: decision, kind: "awaiting_conclusion" })[0].step, "return_answer");
  }
  assert.equal(project({ evidence_unavailable: true })[0].step, "recover_evidence");
  const evidence = "sha256:" + "a".repeat(64);
  assert.deepEqual(project({ evidence_refs: [evidence] })[0].evidence_refs, [evidence]);
  assert.throws(() => project({ evidence_refs: ["/private/report"] }), /opaque SHA256/);
  assert.throws(() => project({ evidence_refs: Array(17).fill(evidence) }), /at most 16/);
  assert.equal(project({ kind: "receipt_unavailable" })[0].step, "recover_evidence");
  assert.throws(() => project({ recorded_decision: "in_progress" }), /unsupported/);
  assert.throws(() => projectReceiverFollowthrough({ observations: Array(21).fill({}) }), /at most 20/);
});

test("partial scope follows the exact current request revision and append-only result", () => {
  const at = "2026-01-01T00:00:00Z";
  const goal_ref = {goal_id: "delivery", goal_instance_id: "ginst_" + "b".repeat(32)};
  const brief = {schema_version: "collaboration_brief_v0", purpose: "Repair the export",
    context: "Keep the installed validation", constraints: [], inputs: [],
    acceptance: ["The installed repair works"], return_requirement: "Return the checked repair"};
  let entry: JsonObject = {...identity, goal_ref, schema_version: "loopx_manager_context_entry_v2",
    source_kind: "peer", source_agent_id: "requester", brief, message: brief.purpose, delivered_at: at};
  const legacy = {...decision, goal_ref, reason: "Accept original scope", decided_at: at};
  const change = (operation: string, patch: JsonObject, actor = "source") => {
    const current = requestControlView(entry, legacy);
    const result = planRequestControl({entry, input: {operation, ...(operation === "assess" ? {} : {
      expected_revision: current.revision, expected_digest: current.revision_digest}), ...patch}, source: {actor,
      actor_agent_id: actor === "source" ? "requester" : "receiver", observed_at: at,
      goal_active: true, legacy_assessment: legacy}});
    entry = result.entry as JsonObject;
  };
  change("revise", {operation_id: "correct-2", brief: {...brief, context: "Use the corrected export"}, reason: "Corrected input"});
  const view = requestControlView(entry, legacy);
  change("assess", {operation_id: "adopt-2", revision: view.revision, revision_digest: view.revision_digest,
    decision: "adopt", reason: "Own this exact correction"}, "receiver");
  const remaining_work = {summary: "Validate the installed export", resume_when: "The package is available"};
  const results: JsonObject[] = [];
  const publish = (update_id?: string, remainder: unknown = remaining_work) => {
    const plan = planCollaborationResult({request: entry, request_revision: 2, legacy_assessment: legacy,
      phase: "conclusion", text: update_id ? "The installed export passed" : "The code is ready",
      remaining_work: remainder, update_id, results});
    const value = plan.value as JsonObject;
    results.push({key: plan.result_key, value, text_chars: [...String(value.text)].length,
      text_sha256: createHash("sha256").update(String(value.text)).digest("hex")});
  };
  const inspect = () => (inspectCollaborationInboxReceipts({observations: [{request: entry,
    route_present: true, decision: read(legacy), read: absent, results}]}).items as JsonObject[])[0];
  publish();
  assert.equal(inspect().kind, "awaiting_conclusion");
  assert.deepEqual(inspect().remaining_work, remaining_work);
  publish("checked", null);
  assert.equal(inspect().kind, "settled");
  assert.equal(inspect().remaining_work, undefined);
  assert.deepEqual((results[0].value as JsonObject).remaining_work, remaining_work);
  change("revise", {operation_id: "correct-3", brief: {...brief, purpose: "Check the new export"}, reason: "New requirement"});
  assert.equal(inspect().kind, "pending");
  assert.equal(inspect().remaining_work, undefined);
  change("cancel", {operation_id: "cancel-3", reason: "Stop only this request"});
  assert.equal(inspect().kind, "settled");
});

test("an orphaned or unreadable update cannot hide an outstanding request", () => {
  const original = {...conclusion, remaining_work: {summary: "Validate the repair", resume_when: "Package ready"}};
  const update = {...identity, decision: "adopt", phase: "conclusion", text: "Later result",
    result_key: "conclusion-00000001", previous_result_key: "conclusion", update_id: "later"};
  const observed = {key: update.result_key, value: update, text_chars: update.text.length,
    text_sha256: createHash("sha256").update(update.text).digest("hex")};
  for (const results of [[observed], [{unavailable: true}]]) {
    const row = (state({decision: read(decision), conclusion: absent, results}) as JsonObject[])[0];
    assert.equal(row.kind, "receipt_unavailable");
  }
  assert.equal((state({decision: read(decision), conclusion: read(original)}) as JsonObject[])[0].kind, "awaiting_conclusion");
});
