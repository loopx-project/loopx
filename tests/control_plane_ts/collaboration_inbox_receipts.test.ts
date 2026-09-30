import assert from "node:assert/strict";
import test from "node:test";
import { inspectCollaborationInboxReceipts } from "../../loopx/control_plane/collaboration/inbox_receipts.ts";

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
