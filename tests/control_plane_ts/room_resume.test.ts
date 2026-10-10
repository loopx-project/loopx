import assert from "node:assert/strict";
import test from "node:test";
import {createHash} from "node:crypto";
import {validateRoomResumeInput, projectRoomResumeReadback} from "../../loopx/control_plane/goals/room_resume.ts";

const quota = {ok: true, status_health_ok: true, goal_id: "goal", mode: "should-run",
  agent_identity: {agent_id: "actor"}, heartbeat_receipt: {status: "committed", turn_instance_id: "turn"},
  selected_todo: {todo_id: "todo", claimed_by: "actor", status: "open"}, should_run: true,
  interaction_contract: {mode: "run", agent_channel: {must_attempt: true, delivery_allowed: true}}};
const projection = {goal_id: "goal", actor_id: "actor", authority_state: "available", source_revision: "revision"};
const ref = "viking://user/example/peers/actor/memories/artifact.json";
const readback = {goal_id: "goal", actor_id: "actor", before_quota: quota, after_quota: quota,
  before_projection: projection, after_projection: projection, before_scope: "scope", after_scope: "scope",
  artifact_scope_refs: ["viking://user/example/peers/actor/memories"],
  recall: {ok: true, status: "applied", context: {guidance: ["remembered approval"]}, application: {receipt: {
    outcome: "applied", current_artifact_verified: true, result_readback_verified: true,
    memory_ref_digests: [createHash("sha256").update(ref).digest("hex").slice(0, 16)]}}},
  artifact_refs: [ref, "viking://user/other/private.json"]};

test("exact Turn/actor identity and current quota are required before retrieval", () => {
  const input = {goal_id: "goal", actor_id: "actor", turn_instance_id: "turn", quota, current_quota: quota, artifact_refs: []};
  assert.equal(validateRoomResumeInput(input).matches, true);
  assert.throws(() => validateRoomResumeInput({...input, actor_id: "other"}));
  assert.throws(() => validateRoomResumeInput({...input, turn_instance_id: "old"}));
  assert.throws(() => validateRoomResumeInput({...input, artifact_refs: ["viking://user/example/../other"]}));
  assert.equal(validateRoomResumeInput({...input, current_quota: {...quota, should_run: false}}).matches, false);
});

test("only explicitly requested references from current accepted recall may be carried", () => {
  const result = projectRoomResumeReadback(readback);
  assert.deepEqual(result.artifact_references, [{ref, source: "current_scoped_recall", target_access_granted: false}]);
  assert.equal(result.omitted_reference_count, 1);
  assert.equal(result.execution_authority_granted, false);
  assert.deepEqual(projectRoomResumeReadback({...readback, artifact_scope_refs: ["viking://user/other"]}).artifact_references, []);
});

test("reconnect discards context when canonical revision, gate, ownership or scope changes", () => {
  for (const delta of [{after_projection: {...projection, source_revision: "new"}}, {after_scope: "revoked"},
    {after_quota: {...quota, should_run: false}}]) {
    const result = projectRoomResumeReadback({...readback, ...delta});
    assert.equal(result.context_usable, false);
    assert.deepEqual(result.artifact_references, []);
  }
  assert.throws(() => projectRoomResumeReadback({...readback, after_quota: {...quota,
    selected_todo: {todo_id: "todo", claimed_by: "other"}}}));
  assert.equal(projectRoomResumeReadback({...readback, recall: {ok: true, status: "provider_unavailable",
    context: {guidance: []}}}).context_usable, false);
});

test("healthy no-work/gated readback is fresh state, while missing scope proof fails closed", () => {
  const empty = projectRoomResumeReadback({...readback, after_quota: {...quota, selected_todo: null, should_run: false}});
  assert.equal(empty.authority_observation_stable, false);
  assert.equal(empty.context_usable, false);
  assert.deepEqual(empty.artifact_references, []);
  for (const delta of [{before_scope: null}, {after_scope: null},
    {after_projection: {...projection, source_revision: null}}]) {
    assert.throws(() => projectRoomResumeReadback({...readback, ...delta}));
  }
  const unverified = projectRoomResumeReadback({...readback, recall: {...readback.recall,
    application: {receipt: {...readback.recall.application.receipt, result_readback_verified: false}}}});
  assert.equal(unverified.context_usable, false);
  assert.deepEqual(unverified.artifact_references, []);
});
