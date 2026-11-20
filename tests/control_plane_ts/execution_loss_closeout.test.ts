import assert from "node:assert/strict";
import test from "node:test";
import {createHash} from "node:crypto";
import {qualifyReceiptBoundExecutionLossCloseout, EXECUTION_LOSS_QUALIFICATION_SCHEMA}
  from "../../loopx/control_plane/quota/execution_loss_closeout.ts";
import {isExecutionLossCloseout} from "../../loopx/control_plane/quota/execution_loss_closeout_record.ts";
import {decodeExecutionLossCloseoutIntent} from "../../loopx/control_plane/quota/execution_loss_closeout.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentity, settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";

function fixture(): JsonObject {
  const identity = settlementIdentityPayload(settlementIdentity({goal_id: "goal", agent_id: "owner",
    todo_id: "todo_loss", turn_instance_id: "original-turn"}));
  const ref = {goal_id: "goal", goal_instance_id: "ginst_" + "a".repeat(32)};
  return {schema_version: EXECUTION_LOSS_QUALIFICATION_SCHEMA, identity, goal_ref: ref,
    receipt: {schema_version: "loopx_rollout_event_v0", event_kind: "quota_should_run", event_id: "original-receipt",
      goal_id: "goal", agent_id: "owner", run_id: "original-turn", status: "normal_run", goal_ref: ref,
      recorded_at: "2026-01-01T00:00:00Z", details: {todo_id: "todo_loss", settlement_effect_id: identity.effect_id,
        closeout_required: true, prepared_lease_key_sha256: createHash("sha256").update("original-key").digest("hex"),
        prepared_lease_version: 119, prepared_lease_epoch: 50, ok: true, should_run: true,
        must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false}},
    current_todo: {todo_id: "todo_loss", role: "agent", task_class: "advancement_task", archive_state: "active",
      status: "open", claimed_by: "owner"}, current_lease: {schema_version: "task_lease_v0", goal_id: "goal",
      todo_id: "todo_loss", owner: "owner", status: "active", idempotency_key: "current-key", version: 121,
      lease_epoch: 51, acquired_at: "2026-01-01T01:00:00Z", updated_at: "2026-01-01T01:01:00Z",
      expires_at: "2026-01-01T02:00:00Z"}, control_writer_proof: {idempotency_key: "current-key", expected_version: 121},
    registered_agents: ["owner", "peer"], provider_identity: "file:" + "a".repeat(32), provider_revision: "revision-1",
    observed_at: "2026-01-01T01:02:00Z", progress_observation: {schema_version: "typed_progress_observation_v0",
      result_class: "blocked", work_item_id: "todo_loss", blocker_id: "execution-lost", evidence_ids: ["evidence:loss"],
      fingerprint: "a".repeat(16)}};
}

test("execution-loss control proof stays distinct from original execution and preserves inputs", () => {
  for (const legacy of [false, true]) {
    const request = fixture();
    if (legacy) {delete request.goal_ref; delete (request.receipt as JsonObject).goal_ref;}
    const before = structuredClone(request);
    const closeout = qualifyReceiptBoundExecutionLossCloseout(request);
    assert.equal(closeout.schema_version, "quota_execution_loss_closeout_v0");
    assert.deepEqual(closeout.original_prepared, {
      prepared_lease_key_sha256: createHash("sha256").update("original-key").digest("hex"),
      prepared_lease_version: 119, prepared_lease_epoch: 50});
    assert.equal((closeout.current_control_writer as JsonObject).version, 121);
    assert.equal((closeout.current_control_writer as JsonObject).lease_epoch, 51);
    assert.equal(isExecutionLossCloseout(closeout, "todo_loss"), true);
    assert.equal(isExecutionLossCloseout({...closeout, completion: true}, "todo_loss"), false);
    assert.equal(isExecutionLossCloseout({...closeout,
      current_control_writer: {...closeout.current_control_writer as JsonObject, idempotency_key: "current-key"}}, "todo_loss"), false);
    assert.equal(JSON.stringify(closeout).includes("current-key"), false);
    assert.deepEqual(request, before);
  }
});

test("execution-loss qualification refuses stale, foreign, ambiguous and incomplete authority", () => {
  const mutations: Array<(request: JsonObject) => void> = [
    r => {(r.receipt as JsonObject).goal_ref = {goal_id: "goal", goal_instance_id: "ginst_" + "b".repeat(32)};},
    r => {delete r.goal_ref;},
    r => {((r.receipt as JsonObject).details as JsonObject).prepared_lease_epoch = 0;},
    r => {delete ((r.receipt as JsonObject).details as JsonObject).prepared_lease_key_sha256;},
    r => {(r.current_lease as JsonObject).lease_epoch = 50;},
    r => {(r.current_lease as JsonObject).owner = "peer";},
    r => {(r.control_writer_proof as JsonObject).expected_version = 120;},
    r => {r.observed_at = "2026-01-01T01:02:00";},
    r => {r.observed_at = "2026-01-01T02:00:00Z";},
    r => {r.registered_agents = ["peer"];},
    r => {(r.current_todo as JsonObject).excluded_agents = ["owner"];},
    r => {(r.current_todo as JsonObject).task_repository = "changed";
      (r.current_lease as JsonObject).write_repository = "original";},
    r => {((r.receipt as JsonObject).details as JsonObject).replan_obligation_id = "replan-" + "a".repeat(16);},
    r => {(r.progress_observation as JsonObject).result_class = "advanced";},
    r => {(r.progress_observation as JsonObject).coverage_complete = true;},
  ];
  for (const mutate of mutations) {
    const request = fixture(); mutate(request);
    assert.throws(() => qualifyReceiptBoundExecutionLossCloseout(request));
  }
});

test("explicit administrative intent cannot carry ordinary business effects", () => {
  const request = fixture();
  const intent = {delivery_outcome: "outcome_gap", delivery_boundary: "semantic_closeout",
    progress_observation: request.progress_observation};
  assert.deepEqual(decodeExecutionLossCloseoutIntent(intent), intent);
  for (const extra of ["next_action", "vision", "completion_todo_id", "explore_result", "usage", "lease_mutation"])
    assert.throws(() => decodeExecutionLossCloseoutIntent({...intent, [extra]: true}));
  assert.throws(() => decodeExecutionLossCloseoutIntent({...intent, delivery_outcome: "outcome_progress"}));
});
