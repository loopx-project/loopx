import assert from "node:assert/strict";
import {test} from "node:test";
import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";
import {effectRuntimeErrorPayload} from "../../loopx/control_plane/effect_runtime_errors.ts";
import {capturedContextSelection} from "../../loopx/control_plane/quota/context_selection.ts";

const select = (context: object, pointers: unknown) => evaluateTurnEnvelope({operation: "context_select", context, select: pointers});

test("batch selection resolves exact escaped addresses and does not mutate the capture", () => {
  const context = {"": {"a/b": {"~": [null, {last: "stop"}]}}, other: true};
  const before = structuredClone(context);
  assert.deepEqual(select(context, ["//a~1b/~0/1", "//a~1b/~0/0", "//a~1b/~0/1"]),
    {selections: [{pointer: "//a~1b/~0/0", path: ["", "a/b", "~", 0]},
      {pointer: "//a~1b/~0/1", path: ["", "a/b", "~", 1]}]});
  assert.deepEqual(context, before);
  assert.deepEqual(select(context, [""]), {selections: [{pointer: "", path: []}]});
});

test("missing, inherited, malformed and noncanonical array addresses fail the whole batch", () => {
  const context = {rows: [null], "01": true};
  for (const pointer of ["rows", "/rows/-", "/rows/01", "/rows/1", "/rows/0/x", "/toString", "/missing", "/~2", "/~"])
    assert.throws(() => select(context, ["/rows", pointer]));
  for (const pointers of [[], null, [1], ["/rows", false]]) assert.throws(() => select(context, pointers));
  assert.deepEqual(select(context, ["/01"]), {selections: [{pointer: "/01", path: ["01"]}]});
});

test("literal prototype-like keys are addressable only as own JSON properties", () => {
  const context = JSON.parse('{"__proto__":{"constructor":false}}');
  assert.deepEqual(select(context, ["/__proto__/constructor"]),
    {selections: [{pointer: "/__proto__/constructor", path: ["__proto__", "constructor"]}]});
  assert.throws(() => select({}, ["/__proto__"]));
});

test("discovery lists exact child addresses without materializing values", () => {
  const context = {"a/b": {"~": ["private-body", null]}, n: 3};
  const result = evaluateTurnEnvelope({operation: "context_select", context,
    select: ["/a~1b", "/a~1b/~0", "/n"], list_fields: true});
  assert.deepEqual(result, {selections: [
    {pointer: "/a~1b", path: ["a/b"], children: [{pointer: "/a~1b/~0", path: ["a/b", "~"]}]},
    {pointer: "/a~1b/~0", path: ["a/b", "~"], children: [
      {pointer: "/a~1b/~0/0", path: ["a/b", "~", 0]}, {pointer: "/a~1b/~0/1", path: ["a/b", "~", 1]}]},
    {pointer: "/n", path: ["n"], children: []}]});
});

test("bounded depth discovers nested addresses in one batch without values", () => {
  const context = {"a/b": {"~": [{stop: "private-value"}]}, scalar: false};
  const request = {operation: "context_select", context, select: ["/a~1b"], list_fields: true};
  assert.deepEqual(evaluateTurnEnvelope({...request, list_depth: 3}), {selections: [
    {pointer: "/a~1b", path: ["a/b"], children: [
      {pointer: "/a~1b/~0", path: ["a/b", "~"]},
      {pointer: "/a~1b/~0/0", path: ["a/b", "~", 0]},
      {pointer: "/a~1b/~0/0/stop", path: ["a/b", "~", 0, "stop"]},
    ]},
  ]});
  assert.deepEqual(evaluateTurnEnvelope({...request, list_depth: 1}), evaluateTurnEnvelope(request));
  for (const list_depth of [0, -1, 9, 1.5, true, "2", null])
    assert.throws(() => evaluateTurnEnvelope({...request, list_depth}));
  assert.throws(() => evaluateTurnEnvelope({...request, list_fields: false, list_depth: 2}));
});


test("selection failures identify original argument and token without echoing content", () => {
  const context = {z: true, rows: [{stop: "private-value"}]};
  for (const [pointer, token] of [["/rows/0/missing", 3], ["/rows/9", 2], ["/rows/01", 2], ["/rows/0/stop/x", 4]] as const) {
    assert.throws(() => select(context, ["/z", "/z", pointer]), (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.match(error.message, new RegExp(`--select argument 3, token ${token}`));
      assert.ok(!error.message.includes("private-value") && !error.message.includes(pointer));
      assert.ok(error.message.length < 240);
      return true;
    });
  }
  assert.throws(() => select(context, ["/z", "secret-".repeat(1000)]), /--select argument 2/);
});

test("selection failures retain machine-readable causes at the runtime boundary", () => {
  const context = {rows: [{stop: "private-value"}]};
  for (const [pointer, code] of [
    ["rows", "context_selection_invalid_pointer"],
    ["/~2", "context_selection_invalid_pointer"],
    ["/rows/01", "context_selection_invalid_index"],
    ["/rows/9", "context_selection_invalid_index"],
    ["/rows/0/missing", "context_selection_missing_pointer"],
    ["/toString", "context_selection_missing_pointer"],
  ]) {
    assert.throws(() => select(context, ["/rows", pointer]), (error: unknown) => {
      const failure = effectRuntimeErrorPayload(error);
      assert.equal(failure.kind, "request_rejected");
      assert.equal(failure.code, code);
      assert.ok(!failure.message.includes(pointer) && !failure.message.includes("private-value"));
      return true;
    });
  }
});

test("full decision discovery points to current controls and whole source obligations before historical detail", () => {
  const context = {
    execution_obligation: {must_attempt_work: false},
    goal_boundary: {stop_condition: "Do not execute the gated action."},
    selected_todo: {todo_id: "todo_current"},
    interaction_contract: {schema_version: "loopx_interaction_contract_v0", user_channel: {notify: "DONT_NOTIFY"},
      agent_channel: {required_reads: ["read the complete accepted criterion"], work_context: {
        instruction: "Read required sources before dependent effects.", sources: [
          {source: "goal", command: "read the current source", content: {text: "A whole goal.\nFinal stop condition.",
            outcome_context: {outcome_revision: 3}, full_criteria: ["Still required before claiming completion."]}},
          {source: "explore", content: {graph: {guidance: "Use relevant evidence.", writeback_results: ["old detailed evidence"]}}},
        ]}}, cli_channel: {next_cli_actions: ["the exact original settlement"], spend_allowed_now: false}},
  };
  const before = structuredClone(context);
  const result = evaluateTurnEnvelope({operation: "context_select", context, select: [""], list_fields: true});
  const pointers = result.initial_read_pointers as string[];
  for (const pointer of ["/execution_obligation", "/goal_boundary", "/selected_todo",
    "/interaction_contract/agent_channel/required_reads", "/interaction_contract/cli_channel/spend_allowed_now",
    "/interaction_contract/agent_channel/work_context/sources/0/content/text"])
    assert.ok(pointers.some(parent => pointer === parent || pointer.startsWith(parent + "/")), pointer);
  assert.ok(!pointers.some(pointer => /writeback_results|full_criteria/.test(pointer)));
  assert.doesNotThrow(() => select(context, pointers));
  // Discovery is not a claim that omitted criteria were read or are optional.
  assert.deepEqual(select(context, ["/interaction_contract/agent_channel/work_context/sources/0/content/full_criteria"]),
    {selections: [{pointer: "/interaction_contract/agent_channel/work_context/sources/0/content/full_criteria",
      path: ["interaction_contract", "agent_channel", "work_context", "sources", 0, "content", "full_criteria"]}]});
  assert.deepEqual(context, before);
  assert.deepEqual(evaluateTurnEnvelope({operation: "context_select", context, initial_read: true}), select(context, pointers));
});

// Compare the selected facts independently of their parent/child packaging.
// Empty containers are facts too; neither omission nor accidental expansion is equivalent.
function selectedFacts(context: unknown, selections: {path: (string | number)[]}[]): string[] {
  const facts = new Set<string>();
  const visit = (value: unknown, path: (string | number)[]) => {
    if (value === null || typeof value !== "object" || !Object.keys(value).length) {
      facts.add(JSON.stringify([path, value]));
      return;
    }
    for (const [key, child] of Object.entries(value))
      visit(child, [...path, Array.isArray(value) ? Number(key) : key]);
  };
  for (const selection of selections) {
    let value = context;
    for (const key of selection.path) value = (value as Record<string | number, unknown>)[key];
    visit(value, selection.path);
  }
  return [...facts].sort();
}

test("initial navigation coalesces complete branches without changing selected facts or explicit readers", () => {
  const context = {facts: [{"a/b": "Keep the final stop.", "~": null}, {allowed: false, evidence: []}],
    single: {only: "A single address need not grow a container."}, unread: {history: "optional body"}};
  // The owner's discovery order differs from object key order. A merged parent
  // occupies its first selected child's position, preserving that priority.
  const pointers = ["/single/only", "/facts/1/evidence", "/facts/0/a~1b", "/facts/0/~0", "/facts/1/allowed"];
  const before = structuredClone(context);
  const explicit = select(context, pointers);
  const initial = capturedContextSelection({context, initial_read: true}, pointers);
  assert.deepEqual(initial, select(context, ["/facts", "/single/only"]));
  assert.deepEqual(selectedFacts(context, initial.selections as {path: (string | number)[]}[]),
    selectedFacts(context, explicit.selections as {path: (string | number)[]}[]));
  assert.deepEqual((explicit.selections as {pointer: string}[]).map(row => row.pointer), [...pointers].sort());
  const discovery = capturedContextSelection({context, select: [""], list_fields: true}, pointers);
  assert.deepEqual(discovery.initial_read_pointers, ["/single/only", "/facts"]);
  assert.deepEqual(discovery.selections, [{pointer: "", path: [], children: [
    {pointer: "/facts", path: ["facts"]}, {pointer: "/single", path: ["single"]}, {pointer: "/unread", path: ["unread"]},
  ]}]);
  assert.deepEqual(context, before);
});

test("ordinary selectors do not inspect initial-navigation data", () => {
  const context = {facts: {allowed: false}};
  const unused = new Proxy<string[]>([], {get() { throw new Error("Initial navigation was not requested"); }});
  for (const pointers of [["/facts"], ["/facts", "/facts"]]) {
    assert.deepEqual(capturedContextSelection({context, select: pointers}, unused), select(context, pointers));
    assert.deepEqual(capturedContextSelection({context, select: pointers, list_fields: true}, unused),
      evaluateTurnEnvelope({operation: "context_select", context, select: pointers, list_fields: true}));
  }
});

test("duplicate root selectors retain the same discovery navigation", () => {
  const context = {facts: {allowed: false, stop: "Preserve the final boundary."}, unread: "optional material"};
  const initial = ["/facts/allowed", "/facts/stop"];
  const single = capturedContextSelection({context, select: [""], list_fields: true}, initial);
  const repeated = capturedContextSelection({context, select: ["", ""], list_fields: true}, initial);
  assert.deepEqual(single.initial_read_pointers, ["/facts"]);
  assert.deepEqual(repeated, single);
  assert.throws(() => capturedContextSelection({context, select: ["", "", "/missing"], list_fields: true}, initial),
    /does not exist/);
});

test("partial coverage never expands unknown or optional facts through a parent", () => {
  const context = {controls: {allowed: false, instruction: "Preserve this constraint.", unknown: "not selected"},
    attachment: {required: false, command: "read the attachment before use", recipe: "deferred recipe"},
    required: {unknown_obligation: "This selected unknown obligation stays whole."}};
  const pointers = ["/controls/allowed", "/controls/instruction", "/attachment/required", "/attachment/command", "/required"];
  const initial = capturedContextSelection({context, initial_read: true}, pointers);
  assert.deepEqual(initial, select(context, pointers));
  const facts = selectedFacts(context, initial.selections as {path: (string | number)[]}[]);
  assert.ok(facts.some(value => value.includes("selected unknown obligation")));
  assert.ok(!facts.some(value => /not selected|deferred recipe/.test(value)));
  assert.throws(() => capturedContextSelection({context, initial_read: true}, [...pointers, "/missing"]), /does not exist/);
});

test("unknown decision schemas and ordinary selections keep generic discovery", () => {
  for (const context of [{value: true}, {interaction_contract: {schema_version: "future"}}]) {
    const result = evaluateTurnEnvelope({operation: "context_select", context, select: [""], list_fields: true});
    assert.equal(result.initial_read_pointers, undefined);
    assert.throws(() => evaluateTurnEnvelope({operation: "context_select", context, initial_read: true}), /generic discovery/);
  }
  const context = {interaction_contract: {schema_version: "loopx_interaction_contract_v0"}};
  assert.equal(select(context, [""]).initial_read_pointers, undefined);
  for (const options of [{select: [""]}, {list_fields: true}, {list_depth: 2}])
    assert.throws(() => evaluateTurnEnvelope({operation: "context_select", context, initial_read: true, ...options}), /cannot be combined/);
});


test("recoverable lookups return only caller positions and nearest container counts, never partial values", () => {
  const context = {z: "private-value", "a/b": {"~": [{leaf: null}]}};
  for (const [pointer, parentCount] of [
    ["/a~1b/~0/8", 2], ["/a~1b/~0/01", 2], ["/a~1b/~0/0/absent", 3],
    ["/a~1b/~0/0/leaf/deeper", 3], ["/z/deeper", 0], ["/absent/deeper", 0],
  ] as const) {
    const result = evaluateTurnEnvelope({operation: "context_select", context,
      select: ["/z", "/z", pointer], recover_selection_errors: true});
    assert.equal(result.selections, undefined);
    assert.deepEqual(Object.keys(result), ["rejected_selection"]);
    const failure = result.rejected_selection as Record<string, unknown>;
    assert.equal(failure.argument_index, 3);
    assert.equal(failure.parent_token_count, parentCount);
    assert.ok(!JSON.stringify(result).includes("private-value"));
    assert.ok(!JSON.stringify(result).includes("a~1b"));
  }
  for (const options of [{recover_selection_errors: false}, {}])
    assert.throws(() => evaluateTurnEnvelope({operation: "context_select", context, select: ["/missing"], ...options}));
  assert.throws(() => evaluateTurnEnvelope({operation: "context_select", context,
    select: ["/~2"], recover_selection_errors: true}), /RFC 6901/);
  assert.throws(() => evaluateTurnEnvelope({operation: "context_select", context,
    select: ["/z"], recover_selection_errors: "true"}), /must be boolean/);
  assert.deepEqual(evaluateTurnEnvelope({operation: "context_select", context,
    select: ["/z"], recover_selection_errors: true}), select(context, ["/z"]));
});
