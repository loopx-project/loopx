import assert from "node:assert/strict";
import test from "node:test";
import {contextSourceIdentity, referenceQuotaSummaryLanes} from "../../loopx/control_plane/quota/diagnostic_projection.ts";

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

test("already shared numeric summary arrays reuse their original identity without new copies", () => {
  const rows = [{todo_id: "task", text: "Keep every original constraint. ".repeat(20), measurement: 1}];
  const source = {agent_todo_summary: {schema_version: "todo_summary_v0", first_executable_items: rows,
    backlog_items_ref: "$.agent_todo_summary.first_executable_items"}};
  const key = contextSourceIdentity(source, [
    [["agent_todo_summary", "first_executable_items", 0, "measurement"], "1.0"],
  ]);
  assert.deepEqual(referenceQuotaSummaryLanes(source, key), source);
});
