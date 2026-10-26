import assert from "node:assert/strict";
import test from "node:test";
import {CONVERSATION_ENTRY_SCHEMA, ORIGINAL_CONVERSATION_RECIPIENT_SCHEMA, normalizeCollaborationRequest,
  sameRequest} from "../../loopx/control_plane/collaboration/semantic_request.ts";
import {initializeControlledRequest, planRequestControl, requestControlView} from "../../loopx/control_plane/collaboration/request_control.ts";
import {configureOriginalConversationGrant, requireOriginalConversationGrant, inspectOriginalConversationGrants} from "../../loopx/control_plane/collaboration/source_grants.ts";
import {planCollaborationResult} from "../../loopx/control_plane/collaboration/result_publication.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const recipient = {schema_version: ORIGINAL_CONVERSATION_RECIPIENT_SCHEMA, binding_id: "a".repeat(24),
  session_id: "original-session", identity_sha256: "b".repeat(64)};
const requester = {goal_ref: {goal_id: "source", goal_instance_id: "ginst_" + "c".repeat(32)}, agent_id: "requester"};
const brief = {schema_version: "collaboration_brief_v0", purpose: "Review the existing artifact", context: "Preserve original context",
  constraints: ["No publication"], inputs: [], acceptance: ["Return independent evidence"], return_requirement: "Return here"};
const at = "2026-01-01T00:00:00Z";
function original(): JsonObject {
  return initializeControlledRequest({schema_version: CONVERSATION_ENTRY_SCHEMA, request_id: "d".repeat(64),
    recipient, requester, source_id: "peer:original", source_kind: "peer", source_agent_id: requester.agent_id,
    brief, delivered_at: at});
}

test("the original conversation profile cannot masquerade as a Goal receiver", () => {
  assert.deepEqual(normalizeCollaborationRequest({recipient, brief}), {recipient, brief});
  for (const extra of [{goal_id: null}, {agent_id: "forged"}, {execution_binding_id: "other"}]) {
    assert.throws(() => normalizeCollaborationRequest({recipient, brief, ...extra}));
    assert.throws(() => initializeControlledRequest({...original(), ...extra}));
  }
  assert.throws(() => initializeControlledRequest({...original(), schema_version: "loopx_manager_context_entry_v2", goal_id: null, goal_ref: null}));
  assert.throws(() => planRequestControl({entry: original(), input: {operation: "reserve"}, source: {}}), /cannot reserve Goal/);
  assert.equal(sameRequest({request_id: "d".repeat(64), goal_id: "source", agent_id: "requester"}, original(), false), false);
});

test("all-registered and workspace grants do not enroll this recipient kind", () => {
  const policy: JsonObject = {schema_version: "loopx_manager_context_policy_v1", sources: {
    channel: {sender_ids: ["owner"], local_delivery_scope: "all_registered", targets: []}}};
  const facts = {policy, requester, recipient, current_recipient: recipient, original_source_id: "source-event",
    parent_request_id: "e".repeat(64), parent_access_verified: true};
  assert.throws(() => requireOriginalConversationGrant(facts), /explicit grant/);
  const observed = inspectOriginalConversationGrants({policy});
  assert.deepEqual(observed.grants, []);
  assert.throws(() => configureOriginalConversationGrant({...facts, grant: true, expected_policy_sha256: "f".repeat(64)}), /policy changed/);
  const plan = configureOriginalConversationGrant({...facts, grant: true, expected_policy_sha256: observed.policy_sha256});
  assert.ok(requireOriginalConversationGrant({...facts, policy: plan.policy}));
  for (const change of [{parent_access_verified: false}, {original_source_id: "other-source"},
    {recipient: {...recipient, session_id: "different-session"}}, {requester: {...requester, agent_id: "other"}}]) {
    assert.throws(() => requireOriginalConversationGrant({...facts, policy: plan.policy, ...change}));
  }
});

test("independent revision-bound read and decision precede a result", () => {
  let entry = original();
  const receiver = {actor: "receiver", actor_recipient: recipient, recipient_active: true, observed_at: at};
  const view = () => requestControlView(entry);
  const run = (input: JsonObject, source: JsonObject = receiver) => {
    const plan = planRequestControl({entry, input, source});
    if (plan.changed) entry = plan.entry as JsonObject;
    return plan;
  };
  const basis = () => ({revision: view().revision, revision_digest: view().revision_digest});
  assert.throws(() => run({operation: "assess", operation_id: "unread", ...basis(), decision: "adopt", reason: "Unread"}), /read this original/);
  run({operation: "read", operation_id: "read-original", ...basis()});
  run({operation: "assess", operation_id: "assess-original", ...basis(), decision: "defer", reason: "Evidence unavailable"});
  const result = planCollaborationResult({request: entry, request_revision: 1, phase: "conclusion", text: "Independent finding", results: []});
  assert.deepEqual((result.value as JsonObject).recipient, recipient);
  assert.deepEqual((result.value as JsonObject).requester, requester);
  assert.equal((result.value as JsonObject).decision, "defer");
  assert.throws(() => run({operation: "cancel", operation_id: "wrong-source", expected_revision: 1,
    expected_digest: view().revision_digest, reason: "Wrong source"}, {actor: "source", actor_agent_id: requester.agent_id,
      actor_requester: {...requester, goal_ref: {...requester.goal_ref, goal_instance_id: "ginst_" + "f".repeat(32)}}, observed_at: at}), /exact identity/);
});
