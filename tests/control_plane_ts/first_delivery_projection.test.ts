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
