import assert from "node:assert/strict";
import test from "node:test";
import {planQueuedChatSteering, selectQueuedChatTurns, QUEUE_STEERING_SCHEMA} from "../../loopx/control_plane/turn_driver/chat_queue_steering.ts";

function facts() {
  return {phase: "prepare", session: {status: "busy", active_turn_id: "active"}, active_status: "running",
    turn: {turn_id: "queued", status: "queued", origin: "web", attachment_count: 0, reservation: null as null | object},
    request: {client_ingress_id: "native-op", operation_id: "op", target_turn_id: "active"},
    receipt: null as null | object, receipt_message_matches: true};
}
const held = {schema_version: QUEUE_STEERING_SCHEMA, status: "pending", client_ingress_id: "native-op", operation_id: "op", target_turn_id: "active"};
const receipt = {status: "delivered", expected_turn_id: "active", active_turn_id: "active", mode: "live_steering"};

test("queued steering requires an unclaimed original and exact live target", () => {
  const f = facts(); assert.equal(planQueuedChatSteering(f).action, "dispatch");
  for (const status of ["starting", "running", "interrupting", "completed", "failed"]) {
    assert.equal(planQueuedChatSteering({...f, turn: {...f.turn, status}}).action, "reject");
  }
  assert.equal(planQueuedChatSteering({...f, session: {...f.session, active_turn_id: "queued"}}).action, "reject");
  assert.equal(planQueuedChatSteering({...f, session: {...f.session, status: "closed"}}).action, "reject");
  assert.deepEqual(planQueuedChatSteering({...f, turn: {...f.turn, attachment_count: 1}}), {action: "reject", error_code: "queue_steering_attachments_unavailable"});
  for (const change of [{origin: "peer"}, {has_collaboration_request: true}, {has_external_agent_target: true}, {loopx_execution: true}]) {
    assert.deepEqual(planQueuedChatSteering({...f, turn: {...f.turn, ...change}}), {action: "reject", error_code: "queue_steering_source_unavailable"});
  }
});

test("uncertain delivery never dispatches again or becomes eligible FIFO work", () => {
  const f = facts(); f.turn.reservation = held;
  for (const state of ["pending", "failed"]) {
    assert.equal(planQueuedChatSteering({...f, receipt: {...receipt, status: state}}).action, "unresolved");
  }
  assert.deepEqual(planQueuedChatSteering({...f, request: {...f.request, client_ingress_id: "other"}}), {action: "reject", error_code: "queue_steering_in_progress"});
  const turns = [{turn_id: "queued", status: "queued", reservation: held}];
  assert.deepEqual(selectQueuedChatTurns({turns}).turn_ids, []);
  assert.deepEqual(selectQueuedChatTurns({turns, include_reserved: true}).turn_ids, ["queued"]);
  const expired = {...turns[0], expires_at: "2025-01-01T00:00:00Z"};
  assert.deepEqual(selectQueuedChatTurns({turns: [expired], include_reserved: true, capacity_at: "2026-01-01T00:00:00Z"}).turn_ids, ["queued"]);
  assert.deepEqual(selectQueuedChatTurns({turns: [{...expired, reservation: null}], include_reserved: true, capacity_at: "2026-01-01T00:00:00Z"}).turn_ids, []);
});

test("acknowledgement recovers after target completion while proven rejection restores queue", () => {
  const f = facts(); f.turn.reservation = held;
  assert.equal(planQueuedChatSteering({...f, session: {status: "ready", active_turn_id: null}, receipt}).action, "complete");
  assert.equal(planQueuedChatSteering({...f, receipt: {...receipt, expected_turn_id: "other"}}).action, "reject");
  assert.equal(planQueuedChatSteering({...f, receipt_message_matches: false, receipt}).action, "reject");
  assert.equal(planQueuedChatSteering({...f, receipt: {...receipt, status: "failed", error_code: "live_steering_turn_mismatch"}}).action, "release");
  assert.equal(planQueuedChatSteering({...f, session: {status: "ready", active_turn_id: null}}).action, "release");
  assert.equal(planQueuedChatSteering({...f, phase: "settle"}).action, "release");
});
