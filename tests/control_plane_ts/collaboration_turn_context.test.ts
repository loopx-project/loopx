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
  const peer = {direction: "outgoing", request: {...request, source_kind: "peer"}};
  assert.equal(project(peer)[0].step, "await_peer_result");
  assert.equal(project({...peer, receipt: settled, results: [conclusion]})[0].step, "consume_peer_result");
  assert.equal(project({request: {...request, source_kind: "peer"}, receipt: settled,
    results: [{...conclusion, status: "delivered"} ]})[0].step, "await_peer_consumption");
  assert.deepEqual(project({...peer, receipt: settled,
    results: [{...conclusion, status: "delivered", consumed: true}]}), []);
  assert.equal(project({direction: "outgoing", receipt: settled, results: [conclusion]})[0].step, "await_return_delivery");
  assert.deepEqual(project({direction: "outgoing", receipt: settled,
    results: [{...conclusion, status: "delivered"}]}), []);
});

test("a returned peer result stays in bounded turn attention even beyond the visible agenda", () => {
  const observations = Array.from({length: 21}, (_, index) => ({
    direction: "outgoing",
    request: {...request, request_id: index.toString(16).padStart(64, "0"), source_kind: "peer"},
    receipt: index === 20 ? settled : {kind: "pending", recorded_decision: null},
    todo_ids: [], evidence_unavailable: false,
    results: index === 20 ? [{...conclusion, status: "delivered"}] : [],
  }));
  const batch = projectDelegationTurnItems({observations}) as Record<string, any>;
  assert.equal(batch.items.length, 20);
  assert.equal(batch.items.some((row: Record<string, unknown>) => row.request_id === "14".padStart(64, "0")), false);
  assert.equal(batch.return_attention.count, 1);
  assert.equal(batch.return_attention.items[0].request_id, "14".padStart(64, "0"));
  assert.deepEqual(Object.keys(batch.return_attention.items[0]).sort(), [
    "agent_id", "request_id", "request_revision", "returns", "step", "unconsumed_return_count",
  ]);

  const result = projectDelegationTurnContext({batches: [batch], scan_complete: true, unavailable_count: 0,
    read_command: "loopx manager-inbox read", observed_at: "2026-01-01T00:00:00Z"});
  const attention = (result.unfinished_delegations as Record<string, any>).return_attention;
  assert.equal(attention.count, 1);
  assert.equal(attention.omitted_count, 0);
  assert.equal(attention.items[0].step, "consume_peer_result");
});

test("an original conversation retains its typed address instead of inventing a Goal receiver", () => {
  const recipient = {schema_version: "collaboration_original_conversation_recipient_v0",
    binding_id: "b".repeat(24), session_id: "original-session", identity_sha256: "c".repeat(64)};
  const peer = {direction: "outgoing", request: {request_id: request.request_id,
    source_kind: "peer", source_agent_id: "requester", recipient, purpose: request.purpose}};
  const row = project(peer)[0];
  assert.deepEqual(row.recipient, recipient);
  assert.equal(row.agent_id, undefined);
  assert.equal(row.goal_ref, undefined);
  assert.equal(row.step, "await_peer_result");
  assert.equal(project({...peer, receipt: settled, results: [conclusion]})[0].step, "consume_peer_result");
  assert.throws(() => project({...peer, request: {...peer.request,
    recipient: {...recipient, goal_id: "replacement"}}}), /unsupported|unexpected|invalid/);
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

test("a correction does not consume an earlier peer result", () => {
  const receipt = {...settled, request_control_view: {revision: 2, control_state: "active", active_reservation: null}};
  const peer = {direction: "outgoing", request: {...request, source_kind: "peer"}, receipt,
    results: [{...conclusion, status: "delivered"},
      {...conclusion, result_key: "conclusion-00000001", request_revision: 2, status: "delivered", consumed: true}]};
  const row = project(peer)[0];
  assert.equal(row?.step, "consume_peer_result");
  assert.equal(row.request_revision, 2);
  assert.equal(row.unconsumed_return_count, 1);
  assert.equal((row.returns as Array<Record<string, unknown>>)[0].request_revision, 1);
  assert.deepEqual(project({...peer, results: peer.results.map(result => ({...result, consumed: true}))}), []);
});

test("a new brief retains unresolved original-route delivery without reopening resolved history", () => {
  const receipt = {...settled, request_control_view: {revision: 2, control_state: "active", active_reservation: null}};
  const current = {...conclusion, result_key: "conclusion-00000001", request_revision: 2, status: "delivered"};
  for (const status of ["queued", "retry_pending", "verification_required", "explicit_unverified", "admitted"]) {
    const row = project({receipt, results: [{...conclusion, status}, current]})[0];
    assert.equal(row.step, "await_return_delivery");
    assert.equal(row.pending_return_count, 1);
    assert.equal((row.returns as Array<Record<string, unknown>>)[0].request_revision, 1);
  }
  for (const status of ["delivered", "superseded"]) {
    assert.deepEqual(project({receipt, results: [{...conclusion, status}, current]}), []);
  }
});

test("a bounded agenda never mistakes omitted or unavailable work for an empty queue", () => {
  const items = Array.from({length: 26}, (_, i) => ({...project()[0], request_id: String(i), direction: i < 23 ? "incoming" : "outgoing"}));
  const result = projectDelegationTurnContext({batches: [{items, incoming_count: 23, outgoing_count: 3,
    return_attention: {count: 0, items: [], omitted_count: 0}}], scan_complete: true, unavailable_count: 0,
    read_command: "loopx manager-inbox read", observed_at: "2026-01-01T00:00:00Z"});
  assert.equal(result.read_complete, false);
  const agenda = result.unfinished_delegations as Record<string, any>;
  assert.equal(agenda.incoming_count, 23);
  assert.equal(agenda.outgoing_count, 3);
  assert.equal(agenda.items.length, 20);
  assert.equal(agenda.items[1].direction, "outgoing");
  assert.equal(agenda.omitted_count, 6);
  assert.deepEqual(agenda.return_attention, {count: 0, items: [], omitted_count: 0});
  assert.equal(agenda.scan_complete, true);
  const unknown = projectDelegationTurnContext({batches: [], scan_complete: true, unavailable_count: 1}).unfinished_delegations as Record<string, unknown>;
  assert.equal(unknown.scan_complete, false);
});

test("return attention aggregation rejects inconsistent bounded counts", () => {
  const item = {request_id: "b".repeat(64), request_revision: 1, step: "consume_peer_result",
    unconsumed_return_count: 1, returns: [{result_key: "conclusion", consumed: false}]};
  const params = {batches: [{items: [], incoming_count: 0, outgoing_count: 1,
    return_attention: {count: 2, items: [item], omitted_count: 0}}], scan_complete: true,
    unavailable_count: 0};
  assert.throws(() => projectDelegationTurnContext(params), /invalid delegation return attention/);
});
