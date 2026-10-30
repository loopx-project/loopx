import assert from "node:assert/strict";
import test from "node:test";
import {evaluateFirstDelivery} from "../../loopx/control_plane/turn_driver/first_delivery.ts";

test("an owner-confirmed exemption continues settlement without claiming a direction commit", () => {
  const writeback_run = {vision_checkpoint: {required: false, satisfied: true, decision: "not_required"}};
  const pending = evaluateFirstDelivery({phase: "project", writeback_run, result_committed: true});
  assert.equal(pending.stage, "settlement_pending");
  assert.equal(pending.direction_required, false);
  assert.equal(pending.direction_committed, false);
  assert.equal(evaluateFirstDelivery({phase: "project", writeback_run, settlement_complete: true}).stage, "settled");
  for (const checkpoint of [undefined, {decision: "not_required"},
    {...writeback_run.vision_checkpoint, required: true}]) {
    assert.equal(evaluateFirstDelivery({phase: "project", result_committed: true,
      writeback_run: {vision_checkpoint: checkpoint}}).stage, "direction_pending");
  }
});

test("an uncommitted result asks for result validation without claiming retained output", () => {
  const projected = evaluateFirstDelivery({phase: "project", result_committed: false});
  assert.equal(projected.stage, "result_review_pending");
  assert.equal(projected.result_committed, false);
  assert.equal(projected.direction_committed, false);
});

test("default pending, failure and stop preserve observed results and noncompletion outcomes", () => {
  const base = {phase: "project", checkpoint_continuation: true, completion_event: {status: "completed"}};
  assert.equal(evaluateFirstDelivery(base).stage, "direction_pending");
  assert.equal(evaluateFirstDelivery({...base, direction_receipt: {direction_stopped: true}}).stage, "direction_stopped");
  assert.equal(evaluateFirstDelivery({...base, direction_receipt: {direction_host_failure: {}}}).stage, "direction_failed");
  const progress = evaluateFirstDelivery({phase: "project", writeback_run: {
    vision_checkpoint: {required: true, satisfied: false, decision: "missing_required"}}});
  assert.equal(progress.stage, "direction_pending");
  assert.equal(progress.result_committed, false);
  assert.equal(progress.outcome_retained, true);
});

test("native committed direction wins over a missing projection flag, but unresolved attempts remain unknown", () => {
  const writeback_run = {vision_checkpoint: {satisfied: true, required: true,
    read_context: {purpose: "first_delivery"}}};
  const direction_receipt = {commit_attempt: {}};
  const projected = evaluateFirstDelivery({phase: "project", writeback_run, direction_receipt});
  assert.equal(projected.stage, "settlement_pending");
  assert.equal(projected.direction_committed, true);
  assert.equal(evaluateFirstDelivery({phase: "project", direction_receipt}).stage, "operation_unknown");
  assert.equal(evaluateFirstDelivery({phase: "project", writeback_run, unknown: true}).stage, "operation_unknown");
});

test("ordinary settlement clears pending while original terminal intent waits for closeout", () => {
  const base = {phase: "project", result_committed: true, settlement_complete: true,
    identity: {effect_id: "original", turn_instance_id: "turn-original"},
    writeback_run: {vision_checkpoint: {satisfied: true, required: true,
      read_context: {purpose: "first_delivery"}}}};
  assert.equal(evaluateFirstDelivery(base).stage, "settled");
  const terminal = {...base, completion_todo: {status: "done", no_followup: false,
    completion_continuation: "active_goal", completion_requested_continuation: "no_followup",
    completion_turn_key: "original"}};
  assert.equal(evaluateFirstDelivery(terminal).stage, "settlement_pending");
  assert.equal(evaluateFirstDelivery({...terminal, terminal_settlement_complete: true}).stage, "settled");
  assert.equal(evaluateFirstDelivery({...terminal,
    completion_todo: {...terminal.completion_todo, completion_turn_key: "another-turn"}}).stage, "settled");
});
