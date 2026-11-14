import assert from "node:assert/strict";
import test from "node:test";
import {contextSourceIdentity, contextProjectionChanges, contextProjectionIdentity, referenceQuotaSummaryLanes,
  resolveQuotaSummaryLanes} from "../../loopx/control_plane/quota/diagnostic_projection.ts";

test("smaller scalar record replacements avoid repeated removal paths and keep sparse edits", () => {
  const source = {summary: {claim_scope: {rows: [{todo_id: "t", text: "Full constraint.", note: "Evidence.",
    status: "open", claimed_by: "peer", priority: "P0", action_kind: "inspect"}]}}};
  const reference = {todo_id: "t", context_ref: "/summary/literal/0"};
  const projected = {summary: {claim_scope: {rows: [reference]}}};
  assert.deepEqual(contextProjectionChanges(source, projected).changes,
    [{path: ["summary", "claim_scope", "rows", 0], value: reference}]);
  const body = "Keep this complete body. ".repeat(100);
  assert.deepEqual(contextProjectionChanges({record: {body, status: "open"}},
    {record: {body, status: "done"}}).changes, [{path: ["record", "status"], value: "done"}]);
});

test("native projection omits only same-path source copies while identity keeps numeric lineage", () => {
  const source = {count: 2 ** 60, moved: 1, old: "unchanged"};
  const projected = {...source, target: source.moved};
  const origins = new WeakMap([[projected, new Map([
    ["count", ["count"]], ["target", ["moved"]],
  ])]]);
  const tokens = [[["count"], "1152921504606846977"], [["moved"], "1.0"]];
  assert.deepEqual(contextProjectionChanges(source, projected, origins, undefined, true).changes,
    [{path: ["target"], from: ["moved"]}]);
  assert.deepEqual(contextProjectionChanges(source, projected, origins).changes,
    [{path: ["count"], from: ["count"]}, {path: ["target"], from: ["moved"]}]);
  assert.notEqual(contextProjectionIdentity(source, projected, tokens, origins),
    contextProjectionIdentity(source, projected, [], origins));
});

test("source identity keeps number spellings separate from strings and retains later exact owners", () => {
  const source = {first: {observations: [1, 2 ** 60]},
    second: {observations: [1, 2 ** 60]}, third: {observations: [1, 2 ** 60]},
    string: {observations: ["1.0", "1152921504606846977"]}};
  const key = contextSourceIdentity(source, [
    [["first", "observations", 1], "1152921504606846976"],
    [["second", "observations", 0], "1.0"],
    [["second", "observations", 1], "1152921504606846977"],
    [["third", "observations", 1], "1152921504606846977"],
    [["third", "observations", 0], "1.0"],
  ]);
  assert.notEqual(key(source.first, ["first"]), key(source.second, ["second"]));
  assert.equal(key(source.second, ["second"]), key(source.third, ["third"]));
  assert.notEqual(key(source.second, ["second"]), key(source.string, ["string"]));
});

for (const tokens of [null, {}, [[[], "1"]], [[["missing"], "1"]],
  [[["value"], "2"]], [[["value"], "NaN"]], [[["value"], "01"]],
  [[["value"], "1"], [["value"], "1.0"]], [[["text"], "1"]],
  [[["rows", "0"], "1.0"]]]) {
  test(`source identity rejects invalid IO tokens: ${JSON.stringify(tokens)}`, () => {
    assert.throws(() => contextSourceIdentity({value: 1, text: "1", rows: [1]}, tokens),
      /context source number token/);
  });
}

test("source edit identities follow retained objects and primitive source references", () => {
  const source = {before: {measurement: 1}, current: {measurement: 1}, raw: -0};
  const tokens = [[["current", "measurement"], "1.0"], [["raw"], "-0.0"]];
  const value = {value: source.raw}, projected = {body: source.current, literal: value};
  const origins = new WeakMap([[value, new Map([["value", ["raw"]]])]]);
  const edits = contextProjectionChanges(source, projected, origins).changes;
  assert.ok(Array.isArray(edits));
  assert.ok(edits.some(edit => JSON.stringify(edit) === JSON.stringify({path: ["literal", "value"], from: ["raw"]})));
  assert.notEqual(contextProjectionIdentity(source, {body: source.before}, tokens),
    contextProjectionIdentity(source, {body: source.current}, tokens));
  assert.notEqual(contextProjectionIdentity(source, {literal: {value: 0}}, tokens),
    contextProjectionIdentity(source, {literal: value}, tokens, origins));
});

test("already shared numeric summary arrays reuse their original identity without new copies", () => {
  const rows = [{todo_id: "task", text: "Keep every original constraint. ".repeat(20), measurement: 1}];
  const source = {agent_todo_summary: {schema_version: "todo_summary_v0", first_executable_items: rows,
    backlog_items_ref: "$.agent_todo_summary.first_executable_items"}};
  const key = contextSourceIdentity(source, [
    [["agent_todo_summary", "first_executable_items", 0, "measurement"], "1.0"],
  ]);
  assert.deepEqual(referenceQuotaSummaryLanes(source, key), source);
});

test("inline summary sharing preserves exact prefixes and keeps changed rows literal", () => {
  const first = {schema_version: "todo_item_v0", todo_id: "first",
    text: "Preserve the complete scope. ".repeat(12), future: {observation: 1}};
  const second = {...first, todo_id: "second"};
  const prefix = [{...first, future: {...first.future}}];
  const source = {agent_todo_summary: {schema_version: "todo_summary_v0",
    first_executable_items: [first, second], backlog_items: prefix,
    claimed_open_items: [{...first, future: {observation: 2}}]}};
  const root = ["agent_todo_summary"];
  const identity = contextSourceIdentity(source, [
    [[...root, "first_executable_items", 0, "future", "observation"], "1.0"],
    [[...root, "backlog_items", 0, "future", "observation"], "1.0"],
  ]);
  const projected = referenceQuotaSummaryLanes(source, identity, true);
  const summary = projected.agent_todo_summary as typeof source.agent_todo_summary & {backlog_items_ref?: string};
  assert.equal(summary.backlog_items_ref, "first_executable_items[0:1]");
  assert.deepEqual(summary.claimed_open_items, source.agent_todo_summary.claimed_open_items);
  assert.deepEqual(resolveQuotaSummaryLanes(projected), source);
  assert.deepEqual(referenceQuotaSummaryLanes(source, identity), source);

  for (const invalid of ["first_executable_items[0:0]", "first_executable_items[0:2]",
    "first_executable_items[1:2]", "$.user_todo_summary.first_executable_items[0:1]"]) {
    assert.throws(() => resolveQuotaSummaryLanes({agent_todo_summary: {...summary, backlog_items_ref: invalid}}),
      /requires one literal array/);
  }
  assert.throws(() => resolveQuotaSummaryLanes({agent_todo_summary: {...summary, backlog_items: prefix}}),
    /requires one literal array/);
  assert.throws(() => resolveQuotaSummaryLanes({agent_todo_summary: {...summary,
    first_executable_items_ref: "$.agent_todo_summary.claimed_open_items"}}), /requires one literal array/);
});

test("claim scope only shares numeric rows with the same original value and spelling", () => {
  const lane = () => [{todo_id: "task", text: "Original peer constraint. ".repeat(20), measurement: 1}];
  const source = {agent_todo_summary: {schema_version: "todo_summary_v0", claimed_by_others_items: lane(),
    claim_scope: {schema_version: "agent_claim_scope_v0", other_agent_claimed_items: lane(),
      blocked_claimed_items: lane()}}};
  const key = contextSourceIdentity(source, [
    [["agent_todo_summary", "claim_scope", "other_agent_claimed_items", 0, "measurement"], "1.0"],
  ]);
  assert.deepEqual(referenceQuotaSummaryLanes(source, key), {agent_todo_summary: {
    ...source.agent_todo_summary, claim_scope: {schema_version: "agent_claim_scope_v0",
      other_agent_claimed_items: lane(), blocked_claimed_items_ref: "$.agent_todo_summary.claimed_by_others_items"},
  }});
});

test("projected source identity tracks retained and moved values without removed tokens", () => {
  const source = {first: {values: [1, 2 ** 60]}, second: {values: [1, 2 ** 60]}};
  const tokens = [
    [["first", "values", 0], "1.0"], [["first", "values", 1], "1152921504606846977"],
    [["second", "values", 0], "1.0"], [["second", "values", 1], "1152921504606846977"],
  ];
  const identity = contextSourceIdentity(source, tokens);
  const projected = {saved: source.first, second_ref: "$.saved"};
  const actual = JSON.parse(identity(projected, []));
  assert.deepEqual(actual[1], [
    [["saved", "values", 0], "1.0"], [["saved", "values", 1], "1152921504606846977"],
  ]);
  const restored = contextSourceIdentity(projected, actual[1]);
  assert.equal(restored(projected, []), identity(projected, []));
  // The complete codec explicitly selects numeric framing. Existing callers
  // without transport metadata retain their original canonical identity.
  assert.equal(contextSourceIdentity({n: 1})({n: 1}, []), '{"n":1}');
  assert.equal(contextSourceIdentity({n: 1}, [])({n: 1}, []), '[{"n":1},[]]');
});
