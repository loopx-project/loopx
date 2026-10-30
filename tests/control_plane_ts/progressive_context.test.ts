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

test("exact repeated Todo rows preserve membership without collapsing distinct evidence or revisions", () => {
  const row = {schema_version: "todo_item_v0", todo_id: "same-title", text: "Shared title.",
    note: "Keep this complete final constraint. ".repeat(20), content_revision: "revision:one",
    claimed_by: "peer", resume_condition: {schema_version: "todo_resume_condition_v0", satisfied: false}};
  const revised = {...row, content_revision: "revision:two", note: "Contrary evidence requires reconsideration."};
  const summary = {schema_version: "todo_summary_v0", first_open_items: [row, revised],
    backlog_items: [row, revised, {...row, claimed_by: "other"}]};
  const payload = {ok: true, agent_todo_summary: summary};
  const before = structuredClone(payload);
  const plan = progressiveContextPlan({surface: "quota_packet", payload});
  assert.deepEqual(plan.references, [
    {path: ["agent_todo_summary", "backlog_items", 0], from: ["agent_todo_summary", "first_open_items", 0],
      value: {todo_id: row.todo_id, context_ref: "/agent_todo_summary/first_open_items/0"}},
    {path: ["agent_todo_summary", "backlog_items", 1], from: ["agent_todo_summary", "first_open_items", 1],
      value: {todo_id: row.todo_id, context_ref: "/agent_todo_summary/first_open_items/1"}},
  ]);
  assert.deepEqual(plan.deferred, []);
  assert.deepEqual(payload, before);
  const pendingSource = {...source, content: {...source.content, read_complete: false}};
  const pendingPlan = progressiveContextPlan({surface: "quota_packet", payload: {...payload,
    interaction_contract: {agent_channel: {work_context: {sources: [pendingSource]},
      required_reads: [{source: "goal_acceptance", reason: FULL_OUTCOME_READ_GUIDANCE}]}}}});
  assert.deepEqual(pendingPlan.deferred, []);
  assert.deepEqual(pendingPlan.instructions.map(row => row.path), [["context_reference_instruction"]]);
  for (const unknown of [{...row, new_obligation: "Keep inline."}, {...row, schema_version: "future"},
    {...row, resume_monitor_generation: 2 ** 60}, {...row, resume_monitor_generation: 1.25}]) {
    const view = {ok: true, agent_todo_summary: {...summary, first_open_items: [unknown], backlog_items: [unknown]}};
    assert.equal(progressiveContextPlan({surface: "quota_packet", payload: view}).references, undefined);
  }
  assert.equal(progressiveContextPlan({surface: "quota_packet", payload: {...payload,
    contract_binding: {signature: "signed"}}}).references, undefined);
});
