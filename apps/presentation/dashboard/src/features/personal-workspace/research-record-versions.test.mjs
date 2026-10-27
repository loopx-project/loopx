import assert from "node:assert/strict";
import {test} from "node:test";
import {researchRecordVersions, changedResearchFields} from "./research-record-versions.ts";

const record = (overrides = {}) => ({goal_id: "goal", event_kind: "finding", result_id: "result", event_id: "old",
  recorded_at: "2026-01-02T00:00:00Z", title: "Observation", summary: "Negative evidence", status: "refuted",
  evidence_refs: ["source:old"], confidence: null, ...overrides});

test("versions retain source order and original evidence, independent of dates and search", () => {
  const old = Object.freeze(record());
  const next = Object.freeze(record({event_id: "new", recorded_at: "2026-01-01T00:00:00Z", summary: "Different scope", status: "confirmed"}));
  assert.deepEqual(researchRecordVersions([old, next], next), [old, next]);
  assert.deepEqual(changedResearchFields(old, next), ["summary", "status"]);
});

test("same title, supersedes and result id cannot cross Goal or kind boundaries", () => {
  const old = record();
  const next = record({event_id: "new"});
  const foreign = record({event_id: "foreign", goal_id: "other", supersedes: "old"});
  const node = record({event_id: "node", event_kind: "node"});
  const unrelated = record({event_id: "unrelated", result_id: "different"});
  assert.deepEqual(researchRecordVersions([foreign, old, node, unrelated, next], next), [old, next]);
  assert.deepEqual(researchRecordVersions([next], next), [next], "Missing history does not synthesize a prior version");
});

test("comparison preserves zero, missing measurements, source changes and original whitespace", () => {
  const old = record({measurements: [{metric: "score", value: null, unit: "points"}]});
  const next = record({summary: "Negative evidence\n", confidence: 0, evidence_refs: [], measurements: [{metric: "score", value: 0, unit: "points"}]});
  assert.deepEqual(changedResearchFields(old, next), ["summary", "evidence_refs", "measurements", "confidence"]);
  assert.deepEqual(changedResearchFields(old, record({measurements: [{unit: "points", value: null, metric: "score"}]})), [], "Object serialization order is not a content change");
  assert.deepEqual(changedResearchFields(old, record()), ["measurements"]);
});
