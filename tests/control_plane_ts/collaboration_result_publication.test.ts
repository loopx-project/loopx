import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { planCollaborationResult, collaborationResultDeliveryReady } from "../../loopx/control_plane/collaboration/result_publication.ts";

const request = { request_id: "a".repeat(64), goal_id: "delivery", agent_id: "worker", source_id: "chat:original" };
const initial = { ...request, phase: "conclusion", text: "Waiting for review.", decision: "adopt" };
function observation(key: string, receipt: JsonObject): JsonObject {
  const { text, ...value } = receipt;
  return { key, value, text_chars: Array.from(String(text)).length, text_sha256: createHash("sha256").update(String(text)).digest("hex") };
}
const input = { request, phase: "conclusion", text: "Review finished.", decision: "adopt", update_id: "review-finished", results: [observation("conclusion", initial)] };

test("a later result appends without replacing the initial conclusion", () => {
  const plan = planCollaborationResult(input);
  assert.equal(plan.result_key, "conclusion-00000001");
  assert.equal(plan.replay, false);
  const value = plan.value as JsonObject;
  assert.equal(value.previous_result_key, "conclusion");
  assert.equal(value.update_id, "review-finished");
  assert.deepEqual(input.results[0], observation("conclusion", initial));
  const replay = planCollaborationResult({ ...input, results: [...input.results, observation(String(plan.result_key), value)] });
  assert.equal(replay.result_key, plan.result_key);
  assert.equal(replay.replay, true);
  assert.throws(() => planCollaborationResult({ ...input, text: "Different fact", results: [...input.results, observation(String(plan.result_key), value)] }), /conflicting/);
});

test("ordinary publication retains immutable phases and rejects invalid update ancestry", () => {
  assert.throws(() => planCollaborationResult({ ...input, update_id: null }), /conflicting/);
  assert.throws(() => planCollaborationResult({ ...input, results: [] }), /initial conclusion/);
  assert.throws(() => planCollaborationResult({ ...input, phase: "decision" }), /conclusion/);
  assert.throws(() => planCollaborationResult({ ...input, update_id: "../escape" }), /update id/);
  assert.throws(() => planCollaborationResult({ ...input, results: [...input.results, ...input.results] }), /duplicate/);
  const plan = planCollaborationResult(input);
  for (const patch of [{ source_id: "another" }, { previous_result_key: "decision" }]) {
    assert.throws(() => planCollaborationResult({ ...input, results: [...input.results, observation(String(plan.result_key), { ...(plan.value as JsonObject), ...patch })] }), /identity|chain/);
  }
});

test("later delivery waits for verified predecessors, including exact update identity", () => {
  const initialUpdate = { previous_result_key: "conclusion" };
  for (const status of ["queued", "retry_pending", "verification_required", "explicit_unverified", "superseded"]) {
    assert.equal(collaborationResultDeliveryReady({ result: initialUpdate, previous_delivery: { status } }).ready, false);
  }
  assert.equal(collaborationResultDeliveryReady({ result: initialUpdate, previous_delivery: { status: "delivered" } }).ready, true);
  const next = { previous_result_key: "conclusion-00000001" };
  assert.equal(collaborationResultDeliveryReady({ result: next, previous_delivery: { status: "delivered" } }).ready, false);
  assert.equal(collaborationResultDeliveryReady({ result: next, previous_delivery: { status: "delivered", result_key: next.previous_result_key } }).ready, true);
  assert.equal(collaborationResultDeliveryReady({ result: {}, previous_delivery: null }).ready, true);
});
