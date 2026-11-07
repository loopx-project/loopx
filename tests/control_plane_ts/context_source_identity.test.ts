import assert from "node:assert/strict";
import test from "node:test";
import {contextSourceIdentity, contextProjectionChanges, contextProjectionIdentity} from "../../loopx/control_plane/quota/diagnostic_projection.ts";

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
