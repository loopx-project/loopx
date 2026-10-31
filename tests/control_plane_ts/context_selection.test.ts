import assert from "node:assert/strict";
import {test} from "node:test";
import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";
import {effectRuntimeErrorPayload} from "../../loopx/control_plane/effect_runtime_errors.ts";

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
