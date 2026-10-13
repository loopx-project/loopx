import assert from "node:assert/strict";
import { normalizeCollaborationRequest, normalizeCollaborationSourceContext, planCollaborationRequestPublication } from "../../loopx/control_plane/collaboration/semantic_request.ts";

const target = { goal_id: "allocation", agent_id: "reviewer" };
const brief = {
  schema_version: "collaboration_brief_v0", purpose: "Review the allocation",
  context: "Proportional rounding was rejected. Preserve the reserve.",
  constraints: ["No real orders"], inputs: [{ ref: "outputs/plan.json", description: "Candidate plan" }],
  acceptance: ["Budget and shared stock hold"], return_requirement: "Return independent findings",
};
assert.deepEqual(normalizeCollaborationRequest(target), target);
assert.deepEqual(normalizeCollaborationRequest({ ...target, brief }), { ...target, brief });
assert.deepEqual(normalizeCollaborationRequest({ ...target, brief, execution_binding_id: "review" }),
  {...target, brief, execution_binding_id: "review"});
assert.throws(() => normalizeCollaborationRequest({...target, execution_binding_id: "review"}));
assert.throws(() => normalizeCollaborationRequest({...target, brief, execution_binding_id: "../escape"}));
for (const request of [
  { ...target, priority: "P0" }, { ...target, agent_id: "../reviewer" },
  { ...target, brief: { ...brief, acceptance: [] } },
  { ...target, brief: { ...brief, authority: "owner" } },
  { ...target, brief: { ...brief, inputs: [{ ref: "../secrets", description: "Outside" }] } },
  { ...target, brief: { ...brief, inputs: [{ ref: "file:///secret", description: "Outside" }] } },
  { ...target, brief: { ...brief, inputs: [{ ref: "inputs/a", description: "Input", sha256: "unverified" }] } },
  { ...target, brief: { ...brief, context: "字".repeat(6000) } },
]) assert.throws(() => normalizeCollaborationRequest(request));
console.log("semantic collaboration contract passed");

const dependency = {ref: "accepted.json", description: "Use the accepted source", sha256: "a".repeat(64),
  delegation: {operation_id: "source-1", ref: "result.json", relation: "uses"}};
assert.deepEqual(normalizeCollaborationRequest({...target, brief: {...brief, inputs: [dependency]}}).brief,
  {...brief, inputs: [dependency]});
for (const patch of [{sha256: undefined}, {delegation: {...dependency.delegation, relation: "adopted"}},
  {delegation: {...dependency.delegation, operation_id: "../other"}},
  {delegation: {...dependency.delegation, ref: "/private/other"}},
  {delegation: {...dependency.delegation, agent_id: "different-requester"}}]) {
  assert.throws(() => normalizeCollaborationRequest({...target, brief: {...brief, inputs: [{...dependency, ...patch}]}}));
}

// Source text uses the same budget for every caller; Unicode characters are
// counted consistently with Python, while encoded bytes protect record reads.
for (const source_message of [" x \n", "x".repeat(32000), "字".repeat(32000), "😀".repeat(24000)]) {
  assert.deepEqual(normalizeCollaborationSourceContext({source_message}), {source_message});
}
for (const source_message of [" ", "x".repeat(32001), "😀".repeat(26000), "\u0000".repeat(17000)]) {
  assert.throws(() => normalizeCollaborationSourceContext({source_message}), /source_message|source context exceeds/);
}

// Only generated guidance and exact entry metadata can differ on replay.
// Semantic context, source, recipient, lifetime and selected route stay bound.
for (const peer of [false, true]) {
  const request = {...target, brief, request_id: "a".repeat(64), source_id: "source-1",
    instruction: "Current generated guide", goal_ref: {goal_id: target.goal_id, goal_instance_id: "ginst_" + "b".repeat(32)},
    ...(peer ? {source_kind: "peer", source_agent_id: "builder", inherited_context: {message: "original context"}} : {})};
  const operation = {...request, instruction: "Original guide"};
  const entry = {...operation, delivered_at: "2026-01-01T00:00:00Z", ...(!peer ? {source_channel: "manager"} : {})};
  const params = {request, operation: peer ? operation : null, entry};
  const before = structuredClone(params);
  assert.deepEqual(planCollaborationRequestPublication(params).entry, entry);
  assert.equal(planCollaborationRequestPublication(params).replayed, true);
  assert.deepEqual(params, before);
  if (peer) {
    assert.deepEqual(planCollaborationRequestPublication({...params, entry: null}), {
      replayed: false, operation, entry: operation,
    });
  }
  for (const change of [
    {source_id: "another-source"}, {agent_id: "another-peer"},
    {goal_ref: {...request.goal_ref, goal_instance_id: "ginst_" + "c".repeat(32)}},
    {brief: {...brief, context: "New request"}}, {host_route: {thread_id: "new-thread"}},
    {unexpected: "extra source field"}, ...(peer ? [{inherited_context: {message: "changed parent"}}] : []),
  ]) {
    assert.throws(() => planCollaborationRequestPublication({...params, request: {...request, ...change}}), /identity conflict/);
  }
  if (peer) {
    assert.throws(() => planCollaborationRequestPublication({...params, entry: {...entry, source_channel: "manager"}}), /identity conflict/);
    assert.throws(() => planCollaborationRequestPublication({...params, operation: {...operation, delivered_at: entry.delivered_at}}), /identity conflict/);
  }
}
