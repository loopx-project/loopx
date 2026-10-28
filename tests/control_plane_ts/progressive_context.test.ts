import assert from "node:assert/strict";
import test from "node:test";
import {progressiveContextPlan} from "../../loopx/control_plane/quota/progressive_context.ts";
import {FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE} from "../../loopx/control_plane/work_items/interaction_contract.ts";

const outcome = {id: "o", objective: "Keep all requirements.", revision: 1, owner_agent_id: "a", todo_ids: ["t"],
  sources: [{ref: "original", summary: "Supporting history."}], requirements: [{id: "r", description: "Final stop.",
    status: "unverified", planning_satisfied: false, agent_review: {id: "r", conclusion: "unmet", note: "Why it failed.",
      evidence_refs: ["negative"], basis_current: false, independently_verified: false}}]};
const source = {source: "goal_acceptance", reason: "Adopt the current objective. " + FULL_OUTCOME_READ_GUIDANCE,
  content: {status: "loaded", goal_acceptance_contract: {outcome_requirements: {
    schema_version: "loopx_goal_outcomes_v0", outcomes: [outcome]}}}};

test("only supporting materials move; cumulative obligations and merged source guidance survive", () => {
  const payload = {ok: true, interaction_contract: {agent_channel: {work_context: {complete: true, sources: [source]}}}};
  const before = structuredClone(payload);
  const plan = progressiveContextPlan({payload, surface: "quota_packet"});
  const parent = ["interaction_contract", "agent_channel", "work_context", "sources", 0,
    "content", "goal_acceptance_contract", "outcome_requirements", "outcomes", 0];
  assert.deepEqual(plan.deferred, [
    {path: [...parent, "sources"], parent, index_key: "supporting_material_pointer"},
    {path: [...parent, "requirements", 0, "agent_review", "note"], parent, index_key: "supporting_material_pointer"},
    {path: [...parent, "requirements", 0, "agent_review", "evidence_refs"], parent, index_key: "supporting_material_pointer"},
  ]);
  assert.deepEqual(plan.instructions, [{path: ["interaction_contract", "agent_channel", "work_context", "sources", 0, "reason"],
    value: "Adopt the current objective. " + PROGRESSIVE_OUTCOME_READ_GUIDANCE}]);
  assert.deepEqual(payload, before);
  assert.deepEqual(progressiveContextPlan({payload, surface: "turn_envelope"}), {deferred: [], instructions: []});
});

test("unknown and failed source semantics remain whole rather than becoming guessed requirements", () => {
  for (const changed of [{...source, content: {...source.content, ok: false}},
    {...source, content: {...source.content, status: "failed"}}, {...source, source: "unregistered"}]) {
    assert.deepEqual(progressiveContextPlan({surface: "quota_packet", payload: {ok: true,
      interaction_contract: {agent_channel: {work_context: {sources: [changed]}}}}}), {deferred: [], instructions: []});
  }
  const custom = {...source, reason: "Unknown independently required instruction."};
  const plan = progressiveContextPlan({surface: "quota_packet", payload: {ok: true,
    interaction_contract: {agent_channel: {work_context: {sources: [custom]}}}}});
  assert.deepEqual(plan.instructions, []);
  assert.deepEqual(plan.deferred, []);
});
