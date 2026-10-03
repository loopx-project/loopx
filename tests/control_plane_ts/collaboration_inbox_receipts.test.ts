import assert from "node:assert/strict";
import test from "node:test";
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
  assert.equal(done.step, "return_answer");
  assert.equal(done.answer_owed, true);
  assert.equal(done.request_completion, "not_established_by_receipts_or_todo_status");
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
