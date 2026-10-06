import assert from "node:assert/strict";
import {comparisonSource, currentComparisonSelection, changedRange, preferredComparisonIndex} from "../src/features/personal-workspace/team-artifact-comparison.ts";

const link = {operation_id: "original", ref: "report.txt", sha256: "a".repeat(64),
  input_ref: "input.txt", relation: "revises", state: "current"};
const source = {operation_id: "original", request_id: "request", agent_id: "author", todo_id: "work",
  status: "accepted", worker_active: false, recovery_required: false,
  artifacts: [{ref: link.ref, sha256: link.sha256, text: "unchanged\nold\nend"}]};
assert.equal(comparisonSource(link, source)?.text, "unchanged\nold\nend");
for (const patch of [{operation_id: "other"}, {status: "rejected"}, {recovery_required: true}, {error: "unavailable"},
  {artifacts: [{ref: link.ref, sha256: "b".repeat(64), text: "new"}]},
  {artifacts: [{ref: "other.txt", sha256: link.sha256, text: "new"}]},
  {artifacts: [...source.artifacts, ...source.artifacts]}]) {
  assert.equal(comparisonSource(link, {...source, ...patch}), null);
}
assert.equal(comparisonSource({...link, state: "unavailable"}, source), null);
const json = {ref: "output.json", sha256: "b".repeat(64), text: "{}"};
const markdown = {ref: "report.md", sha256: "c".repeat(64), text: "# Revised report"};
const otherLink = {...link, operation_id: "review", relation: "responds_to", input_ref: "review.txt"};
const target = {...source, operation_id: "revision", request_id: "revision-request",
  artifacts: [json, markdown], dependencies: [link, otherLink]};
const selection = {sessionId: "conversation", target, link, source, output: markdown};
const retain = result => {
  const current = currentComparisonSelection(selection, result, "conversation");
  assert.equal(current?.output.ref, "report.md");
  assert.equal(current?.output.sha256, markdown.sha256);
  assert.equal(current?.link.operation_id, "original");
  return current;
};
retain(structuredClone(target));
retain({...target, worker_active: true, validation: {source: "todo_validation", basis_sha256: "d".repeat(64), check_count: 1, pinned_file_count: 0}, adoptions: []});
const reordered = {...target, dependencies: [otherLink, {...link}], artifacts: [markdown, json]};
assert.equal(retain(reordered).output, reordered.artifacts[0], "Refresh binds to the selected version, not its old index");
retain({...target, artifacts: [{...json, sha256: "e".repeat(64)}, markdown]});
for (const patch of [{operation_id: "other"}, {request_id: "other"}, {agent_id: "other"}, {todo_id: "other"},
  {status: "rejected"}, {recovery_required: true}, {error: "unavailable"},
  {artifacts: [json]}, {artifacts: [json, {...markdown, sha256: "e".repeat(64)}]},
  {artifacts: [json, {...markdown, ref: "other.md"}]}, {artifacts: [markdown, markdown]},
  {dependencies: [otherLink]}, {dependencies: [link, link]}]) {
  assert.equal(currentComparisonSelection(selection, {...target, ...patch}, "conversation"), null);
}
for (const patch of [{operation_id: "other"}, {ref: "other.txt"}, {sha256: "f".repeat(64)},
  {input_ref: "other.txt"}, {relation: "uses"}, {state: "unavailable"}]) {
  assert.equal(currentComparisonSelection(selection, {...target, dependencies: [{...link, ...patch}, otherLink]}, "conversation"), null);
}
assert.equal(currentComparisonSelection(selection, target, "other-conversation"), null);
assert.equal(currentComparisonSelection({...selection, source: {...source, status: "rejected"}}, target, "conversation"), null);
assert.equal(currentComparisonSelection(null, target, "conversation"), null);
assert.deepEqual(changedRange("unchanged\nold\nend", "unchanged\nnew\nend"), {
  left: ["unchanged", "old", "end"], right: ["unchanged", "new", "end"], start: 1, leftEnd: 2, rightEnd: 2,
});
const equal = changedRange("one\ntwo", "one\ntwo");
assert.equal(equal.start, equal.leftEnd);
assert.equal(equal.start, equal.rightEnd);
const inserted = changedRange("one\nend", "one\nadded\nend");
assert.equal(inserted.leftEnd, 1); assert.equal(inserted.rightEnd, 2);
const removed = changedRange("one\nremoved\nend", "one\nend");
assert.equal(removed.leftEnd, 2); assert.equal(removed.rightEnd, 1);
const outputs = [{ref: "output.json"}, {ref: "review.md"}, {ref: "report.md"}];
assert.equal(preferredComparisonIndex("report.md", outputs), 2);
assert.equal(preferredComparisonIndex("analysis.MD", outputs), 1);
assert.equal(preferredComparisonIndex("source.json", outputs), 0);
assert.equal(preferredComparisonIndex("no-extension", outputs), 0);
assert.equal(preferredComparisonIndex("source.txt", outputs), 0);
console.log("team artifact comparison: exact-version retention, identity/change/ambiguity rejection and changed-range cases passed");
