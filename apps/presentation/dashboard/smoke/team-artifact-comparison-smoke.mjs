import assert from "node:assert/strict";
import {comparisonSource, changedRange} from "../node_modules/.cache/team-comparison/features/personal-workspace/team-artifact-comparison.js";

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
console.log("team artifact comparison: exact-version rejection and changed-range cases passed");
