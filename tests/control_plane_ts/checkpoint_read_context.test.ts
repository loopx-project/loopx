import assert from "node:assert/strict";
import test from "node:test";
import {evaluateCheckpointReadContext as evaluate} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

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

test("first delivery has independent admission and cannot reuse a supplement receipt", () => {
  const first = {purpose: "first_delivery", decision_scope: "agent_lane", prior: null, admitted_turn: true};
  const receipt = read(first).receipt;
  assert.equal(check(receipt, first).ok, true);
  assert.equal(read({...first, admitted_turn: false}).error_code, "checkpoint_first_delivery_not_admitted");
  assert.equal(read({...first, prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}}}).ok, false);
  assert.equal(check(receipt).ok, false);
  assert.equal(check(read().receipt, first).ok, false);
});

test("first direction detects frontier membership while ignoring independent peer work", () => {
  const first = {purpose: "first_delivery", decision_scope: "agent_lane", prior: null, admitted_turn: true};
  const source = {...facts, todos: [...facts.todos, {todo_id: "peer", claimed_by: "other", status: "open"}] as JsonObject[]};
  const receipt = read({...first, facts: source}).receipt;
  const changed = structuredClone(source);
  changed.todos[4].status = "done";
  assert.equal(check(receipt, {...first, facts: changed}).ok, true);
  changed.todos.push({todo_id: "new", claimed_by: "agent", status: "open"});
  assert.deepEqual(check(receipt, {...first, facts: changed}).changed_components, ["frontier"]);
  changed.todos.pop();
  changed.todos[4].claimed_by = "agent";
  assert.deepEqual(check(receipt, {...first, facts: changed}).changed_components, ["frontier"]);
  const whole = read({...first, decision_scope: "goal", facts: source}).receipt;
  assert.equal(check(whole, {...first, decision_scope: "agent_lane", facts: source}).error_code,
    "checkpoint_read_context_scope_mismatch");
  assert.equal(check(whole, {...first, decision_scope: "goal", facts: changed}).ok, false);
});

test("first result and direction bind authority, lease expiry, purpose and unknown fields", () => {
  for (const purpose of ["first_delivery", "delivery_result"]) {
    const request = {purpose, decision_scope: "goal", prior: null, admitted_turn: true};
    const baseline = {...facts, execution_lease: {owner: "agent", active: true},
      source: {authority: "file_v0", store_identity: "store-1", registry_goal: {registered_agents: ["agent"]}}};
    const receipt = read({...request, facts: baseline}).receipt;
    for (const change of [
      {...baseline, execution_lease: {owner: "agent", active: false}},
      {...baseline, execution_lease: {owner: "other", active: true}},
      {...baseline, source: {...baseline.source, store_identity: "store-2"}},
      {...baseline, source: {...baseline.source, registry_goal: {registered_agents: []}}},
      {...baseline, todos: baseline.todos.map((row, i) => i === 0 ? {...row, future_business_rule: "changed"} : row)},
    ]) assert.equal(check(receipt, {...request, facts: change}).error_code, "checkpoint_read_context_stale");
    assert.equal(check(receipt, {...request, purpose: purpose === "first_delivery" ? "delivery_result" : "first_delivery"}).ok, false);
  }
});

test("an uncertain direction cannot acquire a replacement read or reexecute", () => {
  const request = {purpose: "first_delivery", decision_scope: "goal", prior: null, admitted_turn: true};
  const receipt = {...read(request).receipt as JsonObject, commit_attempt: {json_path: "unindexed"}};
  for (const result of [read({...request, receipt}), check(receipt, request)]) {
    assert.equal(result.error_code, "checkpoint_commit_unknown");
    assert.equal(result.reread_required, false);
  }
});
