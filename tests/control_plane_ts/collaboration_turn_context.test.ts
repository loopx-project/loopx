import assert from "node:assert/strict";
import test from "node:test";
import {projectDelegationTurnItems, projectDelegationTurnContext} from "../../loopx/control_plane/collaboration/turn_context.ts";
const request = {request_id: "a".repeat(64), agent_id: "worker", purpose: "Qualify the current artifact"};
const observation = (patch: object = {}) => ({direction: "incoming", request,
  receipt: {kind: "awaiting_conclusion", recorded_decision: "adopt"}, todo_ids: ["todo_artifact"],
  evidence_unavailable: false, results: [], ...patch});
const project = (patch: object = {}) => projectDelegationTurnItems({observations: [observation(patch)]}).items as Array<Record<string, unknown>>;
const settled = {kind: "settled", recorded_decision: "adopt"};
const conclusion = {result_key: "conclusion", request_revision: 1, status: "queued", consumed: false};

test("an already assessed or Todo-linked request remains until actual answer return", () => {
  assert.equal(project()[0].step, "review_request_work");
  assert.deepEqual(project()[0].todo_ids, ["todo_artifact"]);
  assert.equal(project({receipt: {kind: "pending", recorded_decision: null}})[0].step, "assess_request");
  assert.equal(project({receipt: {kind: "awaiting_conclusion", recorded_decision: "defer"}})[0].step, "return_answer");
  for (const status of ["queued", "retry_pending", "verification_required", "explicit_unverified", "admitted"]) {
    assert.equal(project({receipt: settled, results: [{...conclusion, status} ]})[0].step, "await_return_delivery");
  }
  assert.deepEqual(project({receipt: settled, results: [{...conclusion, status: "delivered"}]}), []);
});

test("sent work remains visible before return and until the requester consumes it", () => {
  assert.equal(project({direction: "outgoing"})[0].step, "await_peer_result");
  assert.equal(project({direction: "outgoing", receipt: settled, results: [conclusion]})[0].step, "consume_peer_result");
  assert.equal(project({request: {...request, source_kind: "peer"}, receipt: settled,
    results: [{...conclusion, status: "delivered"} ]})[0].step, "await_peer_consumption");
  assert.deepEqual(project({direction: "outgoing", receipt: settled,
    results: [{...conclusion, status: "delivered", consumed: true}]}), []);
});

test("current revisions, cancellation and unavailable receipts retain independent meanings", () => {
  const control = {revision: 2, revision_digest: "sha256:" + "b".repeat(64), brief: {purpose: "Revised requirement"},
    control_state: "active", active_reservation: null};
  const receipt = {kind: "awaiting_conclusion", recorded_decision: "adopt", request_control_view: control};
  const row = project({receipt, results: [{...conclusion, status: "delivered", consumed: true}]})[0];
  assert.equal(row.step, "review_request_work");
  assert.equal(row.purpose, "Revised requirement");
  assert.equal(row.request_revision, 2);
  assert.equal(row.returns, undefined);
  assert.deepEqual(project({receipt: {...receipt, request_control_view: {...control, control_state: "cancelled"}}}), []);
  assert.equal(project({receipt: {...receipt, request_control_view: {...control,
    control_state: "cancelled", active_reservation: {reservation_id: "reserved"}}}})[0].step, "reconcile_reservation");
  assert.equal(project({evidence_unavailable: true})[0].step, "recover_evidence");
  assert.equal(project({receipt: {kind: "receipt_unavailable", recorded_decision: null}})[0].step, "recover_evidence");
});

test("a bounded agenda never mistakes omitted or unavailable work for an empty queue", () => {
  const items = Array.from({length: 26}, (_, i) => ({...project()[0], request_id: String(i), direction: i < 23 ? "incoming" : "outgoing"}));
  const result = projectDelegationTurnContext({batches: [{items, incoming_count: 23, outgoing_count: 3}], scan_complete: true, unavailable_count: 0,
    read_command: "loopx manager-inbox read", observed_at: "2026-01-01T00:00:00Z"});
  assert.equal(result.read_complete, false);
  const agenda = result.unfinished_delegations as Record<string, any>;
  assert.equal(agenda.incoming_count, 23);
  assert.equal(agenda.outgoing_count, 3);
  assert.equal(agenda.items.length, 20);
  assert.equal(agenda.items[1].direction, "outgoing");
  assert.equal(agenda.omitted_count, 6);
  assert.equal(agenda.scan_complete, true);
  const unknown = projectDelegationTurnContext({batches: [], scan_complete: true, unavailable_count: 1}).unfinished_delegations as Record<string, unknown>;
  assert.equal(unknown.scan_complete, false);
});
