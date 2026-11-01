import assert from "node:assert/strict";
import test from "node:test";
import {evaluateCheckpointReadContext as evaluate} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentity} from "../../loopx/control_plane/effect_program.ts";

const identity = {goal_id: "goal", agent_id: "agent", todo_id: "task", turn_instance_id: "turn", effect_id: "effect"};
const facts = {
  todos: [
    {todo_id: "task", status: "open", text: "Build a page", depends_on_todo_ids: ["upstream"]},
    {todo_id: "upstream", status: "done", evidence: "result:1", depends_on_todo_id: "ancestor"},
    {todo_id: "ancestor", status: "done", evidence: "basis:1"},
    {todo_id: "unrelated", status: "open"},
  ],
  frontmatter: {objective: "A minimal page", updated_at: "old"}, goal_prose: "Acceptance: page renders",
  acceptance: {revision: 1, contract: {criteria: ["page renders"]}},
  agent_vision: {summary: "Build the page"}, source: {authority: "legacy_markdown"},
};
const read = (overrides: JsonObject = {}) => evaluate({phase: "read", identity, facts,
  prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}},
  dependency_todo_ids: [], read_context_id: "read-A", ...overrides});
const check = (receipt: unknown, overrides: JsonObject = {}) => evaluate({
  phase: "check", identity, facts, read_context_id: "read-A", receipt, ...overrides,
});

test("read captures the exact task, transitive upstream results and goal acceptance", () => {
  const result = read();
  const basis = result.basis as JsonObject;
  assert.equal(result.ok, true);
  assert.deepEqual((basis.dependencies as JsonObject[]).map(row => row.todo_id), ["ancestor", "upstream"]);
  assert.equal(check(result.receipt).ok, true);
});

test("an unqualified blocked replan needs the exact committed source and no debit before reread", () => {
  const bound = settlementIdentity({goal_id: "goal", agent_id: "agent", turn_instance_id: "turn",
    replan_obligation_id: "replan-1111111111111111"});
  const prior = {goal_id: "goal", agent_id: "agent", turn_instance_id: "turn",
    replan_obligation_id: bound.replan_obligation_id, delivery_outcome: "outcome_gap",
    progress_observation: {schema_version: "typed_progress_observation_v0", result_class: "blocked",
      work_item_id: bound.replan_obligation_id, blocker_id: "blocker:source", evidence_ids: ["evidence:read"]},
    vision_checkpoint: {decision: "unchanged_with_reason", satisfied: true}};
  const pending = {identity: bound, prior, writeback_committed: true, quota_spend_committed: false,
    semantic_replan_guard: {selected_obligation_id: bound.replan_obligation_id}};
  assert.equal(read(pending).ok, true);
  for (const changed of [{...pending, writeback_committed: false}, {...pending, quota_spend_committed: true},
    {...pending, semantic_replan_guard: {}}, {...pending, prior: {...prior, agent_id: "other-agent"}},
    {...pending, prior: {...prior, autonomous_replan_ack: {recorded: true}}}]) {
    assert.equal(read(changed).error_code, "checkpoint_context_not_missing");
  }
});

test("old receipts and identical content from a different provider lineage require reread", () => {
  const source = {...facts.source, store_identity: "file:first"};
  const receipt = read({facts: {...facts, source}}).receipt as JsonObject;
  assert.equal(check({...receipt, schema_version: "checkpoint_read_context_v0"}).error_code,
    "checkpoint_read_context_unknown_or_replaced");
  const changed = check(receipt, {facts: {...facts, source: {...source, store_identity: "file:restored"}}});
  assert.equal(changed.error_code, "checkpoint_read_context_stale");
  assert.deepEqual(changed.changed_components, ["source"]);
});

test("each changed decision input rejects without permitting an append", () => {
  const receipt = read().receipt;
  for (const [component, mutate] of [
    ["todo", (value: typeof facts) => {value.todos[0].text = "Build a form";}],
    ["todo", (value: typeof facts) => {value.todos[0].status = "done";}],
    ["todo", (value: typeof facts) => {value.todos[0].depends_on_todo_ids = ["unrelated"];}],
    ["dependencies", (value: typeof facts) => {value.todos[1].evidence = "result:2";}],
    ["dependencies", (value: typeof facts) => {value.todos[2].evidence = "basis:2";}],
    ["goal", (value: typeof facts) => {value.acceptance.revision++;}],
    ["goal", (value: typeof facts) => {value.goal_prose = "Acceptance: include authentication";}],
    ["agent_vision", (value: typeof facts) => {value.agent_vision.summary = "A different route";}],
    ["source", (value: typeof facts) => {value.source.authority = "sqlite_v0";}],
  ] as const) {
    const current = structuredClone(facts);
    mutate(current);
    const result = check(receipt, {facts: current});
    assert.equal(result.error_code, "checkpoint_read_context_stale");
    assert.ok((result.changed_components as string[]).includes(component));
    assert.equal(result.reread_required, true);
  }
});

test("an unrelated task and display/timestamp changes do not invalidate the basis", () => {
  const current = structuredClone(facts);
  current.todos[3].status = "done";
  current.frontmatter.updated_at = "new";
  Object.assign(current.todos[0], {index: 99, source_section: "Agent Todo"});
  assert.equal(check(read().receipt, {facts: current}).ok, true);
});

test("legacy User Todo requirements remain covered even without an assigned Todo id", () => {
  const basis = {...facts, todos: [...facts.todos, {role: "user", text: "Owner requires a keyboard check"}]};
  const receipt = read({facts: basis}).receipt;
  const current = {...basis, todos: [...facts.todos, {role: "user", text: "Owner requires a screen reader check"}]};
  const result = check(receipt, {facts: current});
  assert.equal(result.error_code, "checkpoint_read_context_stale");
  assert.deepEqual(result.changed_components, ["goal"]);
});

test("extra used results are captured; missing dependencies and duplicate identities fail closed", () => {
  const receipt = read({dependency_todo_ids: ["unrelated"]}).receipt;
  const current = structuredClone(facts);
  current.todos[3].status = "done";
  assert.equal(check(receipt, {facts: current}).error_code, "checkpoint_read_context_stale");
  assert.throws(() => read({dependency_todo_ids: ["absent"]}), /absent/);
  assert.throws(() => read({facts: {...facts, todos: [...facts.todos, facts.todos[0]]}}), /duplicate/);
  assert.throws(() => read({facts: {...facts, todos: facts.todos.slice(1)}}), /Todo is absent/);
});

test("parallel reads cannot relabel an old operation as the latest read", () => {
  const a = read().receipt;
  const b = read({read_context_id: "read-B"}).receipt;
  assert.equal(check(a).ok, true);
  assert.equal(check(b).error_code, "checkpoint_read_context_unknown_or_replaced");
  assert.equal(check(b, {read_context_id: "read-B"}).ok, true);
  assert.equal(check(a, {read_context_id: null}).error_code, "checkpoint_read_context_required");
  assert.equal(check(null).error_code, "checkpoint_read_context_unknown_or_replaced");
  for (const field of ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"]) {
    assert.equal(check(a, {identity: {...identity, [field]: "different"}}).error_code,
      "checkpoint_read_context_identity_mismatch");
  }
});

test("obligations cover the full frontier and a completed checkpoint cannot acquire another receipt", () => {
  const scope = {...identity, todo_id: null, replan_obligation_id: "obligation"};
  const receipt = read({identity: scope}).receipt;
  const current = structuredClone(facts);
  current.todos[3].status = "done";
  assert.equal(check(receipt, {identity: scope, facts: current}).error_code, "checkpoint_read_context_stale");
  assert.equal(read({prior: {vision_checkpoint: {decision: "patched", satisfied: true}}}).error_code,
    "checkpoint_context_not_missing");
});
