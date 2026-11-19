import assert from "node:assert/strict";
import test from "node:test";
import {planChatSteeringReadback, planNativeMentionIngress, planQueuedChatMutation, planQueuedChatSteering, selectQueuedChatTurns, QUEUE_STEERING_SCHEMA} from "../../loopx/control_plane/turn_driver/chat_queue_steering.ts";

function facts() {
  return {phase: "prepare", session: {status: "busy", active_turn_id: "active"}, active_status: "running",
    turn: {turn_id: "queued", status: "queued", origin: "web", attachment_count: 0, reservation: null as null | object},
    request: {client_ingress_id: "native-op", operation_id: "op", target_turn_id: "active"},
    receipt: null as null | object, receipt_message_matches: true};
}
const held = {schema_version: QUEUE_STEERING_SCHEMA, status: "pending", client_ingress_id: "native-op", operation_id: "op", target_turn_id: "active"};
const receipt = {status: "delivered", expected_turn_id: "active", active_turn_id: "active", mode: "live_steering"};

test("native readback requires the saved client ID and its original thread, Turn and full text", () => {
  const native = {local_turn_id: "active", thread_id: "thread", turn_id: "native-turn", client_message_id: "message-id"};
  const original = {...receipt, status: "failed", message: "Line one.\nLine two.", native_steering: native};
  const input = {receipt: original, session_thread_id: "thread", adapter_thread_id: "thread",
    turn: {turn_id: "active", upstream_turn_id: "native-turn"}};
  const entry = {turnId: "native-turn", item: {type: "userMessage", clientId: "message-id",
    content: [{type: "text", text: original.message}]}};
  assert.equal(planChatSteeringReadback(input).action, "read");
  assert.equal(planChatSteeringReadback({...input, items: [entry], inventory_complete: true}).action, "confirmed");
  assert.equal(planChatSteeringReadback({...input, items: [entry], inventory_complete: false}).action, "unresolved");
  for (const change of [
    {session_thread_id: "new-thread"}, {adapter_thread_id: "new-thread"},
    {turn: {...input.turn, turn_id: "new-turn"}},
    {turn: {...input.turn, upstream_turn_id: "new-native-turn"}},
    {receipt: {...original, expected_turn_id: "new-turn"}},
    {receipt: {...original, native_steering: null}},
    {receipt: {...original, native_steering: {...native, client_message_id: ""}}},
  ]) assert.equal(planChatSteeringReadback({...input, ...change, items: [entry], inventory_complete: true}).action, "unresolved");
  for (const items of [[], [entry, entry], [{...entry, turnId: "another-turn"}],
    [{...entry, item: {...entry.item, clientId: "another-id"}}],
    [{...entry, item: {...entry.item, content: [{type: "text", text: "Different input"}]}}],
    [{...entry, item: {...entry.item, content: [...entry.item.content, {type: "text", text: "Extra"}]}}],
  ]) assert.equal(planChatSteeringReadback({...input, items, inventory_complete: true}).action, "unresolved");
});

test("input changes require the exact unclaimed web input revision, without native steering", () => {
  const f = facts();
  const request = {action: "edit", expected_revision: 0, operation_id: "edit-1", message: "Corrected input"};
  const input = {...f, request};
  assert.equal(planQueuedChatMutation(input).action, "apply");
  for (const status of ["starting", "running", "completed", "failed", "interrupted"]) {
    assert.equal(planQueuedChatMutation({...input, turn: {...input.turn, status}}).error_code, "queue_turn_already_started");
  }
  assert.equal(planQueuedChatMutation({...input, turn: {...input.turn, input_revision: 1}}).error_code, "queue_input_revision_conflict");
  for (const extra of [{origin: "external"}, {origin: "peer"}, {has_collaboration_request: true}, {loopx_execution: true}, {has_acceptance: true}]) {
    assert.equal(planQueuedChatMutation({...input, turn: {...input.turn, ...extra}}).error_code, "queue_mutation_source_unavailable");
  }
  assert.equal(planQueuedChatMutation({...input, turn: {...input.turn, reservation: held}}).error_code, "queue_steering_in_progress");
  assert.throws(() => planQueuedChatMutation({...input, request: {...request, message: " "}}));
  assert.throws(() => planQueuedChatMutation({...input, request: {...request, action: "cancel"}}));
});

test("partial input writes exclude both FIFO and steering; exact retries finish or replay", () => {
  const f = facts();
  const request = {action: "cancel", expected_revision: 0, operation_id: "cancel-1"};
  const pending = { ...request, input_revision: 1 };
  const turn = {...f.turn, mutation: pending};
  assert.deepEqual(selectQueuedChatTurns({turns: [turn]}).turn_ids, []);
  assert.deepEqual(selectQueuedChatTurns({turns: [turn], include_reserved: true}).turn_ids, ["queued"]);
  assert.deepEqual(selectQueuedChatTurns({turns: [{...turn, expires_at: "2020-01-01T00:00:00Z"}],
    capacity_at: "2026-01-01T00:00:00Z", include_reserved: true}).turn_ids, ["queued"]);
  assert.equal(planQueuedChatSteering({...f, turn}).error_code, "queue_mutation_in_progress");
  assert.equal(planQueuedChatMutation({...f, turn, request}).action, "apply");
  assert.equal(planQueuedChatMutation({...f, turn, request: {...request, operation_id: "different"}}).error_code, "queue_mutation_in_progress");
  const ended = {...f.turn, status: "interrupted", input_revision: 1, operations: [pending]};
  assert.equal(planQueuedChatMutation({...f, turn: ended, request}).action, "replay");
  assert.equal(planQueuedChatMutation({...f, turn: ended, request: {...request, expected_revision: 1}}).error_code, "queue_mutation_identity_conflict");
});

test("queue input actions are literal strings in requests and stored receipts", () => {
  const f = facts();
  const request = {action: "cancel", expected_revision: 0, operation_id: "strict-input"};
  for (const action of [null, false, 0, {}, [], ["edit"], ["cancel"], [["edit"]], {toString: "edit"}, "unknown"]) {
    assert.throws(() => planQueuedChatMutation({...f, request: {...request, action}}), /Invalid queue input request/);
    const invalid = {...request, action, input_revision: 1};
    assert.throws(() => selectQueuedChatTurns({turns: [{...f.turn, mutation: invalid}], include_reserved: true}),
      /Invalid queue input receipt/);
    for (const extra of [{mutation: invalid}, {operations: [invalid]}]) {
      assert.throws(() => planQueuedChatMutation({...f, request, turn: {...f.turn, ...extra}}),
        /Invalid queue input receipt/);
    }
  }
});

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

test("native mention admission preserves the observed target and prior ingress", () => {
  const input = {mentioned: true, binding_matches: true, text_only: true, may_deliver_new: true,
    target_turn_id: "first", receipt_message_matches: true};
  assert.deepEqual(planNativeMentionIngress({...input, mentioned: false}), {action: "configured"});
  assert.deepEqual(planNativeMentionIngress(input), {action: "steer", target_turn_id: "first"});
  assert.deepEqual(planNativeMentionIngress({...input, target_turn_id: null}), {action: "enqueue"});
  assert.deepEqual(planNativeMentionIngress({...input, may_deliver_new: false, target_turn_id: "later"}),
    {action: "unresolved"});
  assert.deepEqual(planNativeMentionIngress({...input, queued_turn_exists: true}), {action: "enqueue"});
  assert.equal(planNativeMentionIngress({...input, binding_matches: false}).action, "reject");
  assert.equal(planNativeMentionIngress({...input, text_only: false}).error_code, "queue_steering_attachments_unavailable");
  const receipt = {mode: "live_steering", status: "delivered", expected_turn_id: "first", active_turn_id: "first"};
  assert.deepEqual(planNativeMentionIngress({...input, receipt, may_deliver_new: false, target_turn_id: "later"}),
    {action: "delivered", target_turn_id: "first"});
  for (const uncertain of [{...receipt, status: "pending"}, {...receipt, active_turn_id: "later"},
    {...receipt, status: "failed", error_code: "live_steering_rejected"}]) {
    assert.deepEqual(planNativeMentionIngress({...input, receipt: uncertain}), {action: "unresolved"});
  }
  assert.deepEqual(planNativeMentionIngress({...input, receipt, receipt_message_matches: false}), {action: "unresolved"});
  assert.equal(planNativeMentionIngress({...input, receipt: {...receipt, status: "failed",
    error_code: "live_steering_turn_mismatch"}}).action, "reject");
  assert.equal(planNativeMentionIngress({...input, receipt: {mode: "session_queue"}}).action, "enqueue");
});
