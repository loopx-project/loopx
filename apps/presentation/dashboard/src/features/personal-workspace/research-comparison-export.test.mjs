import assert from "node:assert/strict";
import test from "node:test";
import {exportResearchComparison, researchComparisonSnapshot} from "./research-comparison-export.ts";
import {changedResearchFields} from "./research-record-versions.ts";
import {researchRecordSnapshot} from "./research-record-export.ts";

const record = (overrides = {}) => ({schema_version: "explore_finding_v0", goal_id: "synthetic", event_id: "old",
  event_kind: "finding", result_id: "retained", recorded_at: "2026-01-02T00:00:00Z", title: "Original negative result",
  summary: "Earlier evidence", status: "refuted", confidence: "recorded", evidence_refs: ["synthetic:old"], ...overrides});
const old = record({measurements: [{metric: "Score", value: null, evidence_ref: "synthetic:unknown"}]}),
  next = record({event_id: "next", title: "Later result", status: "confirmed", run_id: "run:new",
    measurements: [{metric: "Score", value: 0, evidence_ref: "synthetic:zero"}]}),
  third = record({event_id: "third", title: "Open question", status: "tentative", measurements: []});
const input = (patch = {}) => ({events: [old, next, third], selectedId: "old", comparisonId: "next", complete: true,
  stale: false, exportedAt: "2026-01-04T00:00:00Z", ...patch});

test("exact pair includes the comparison outside generic search and uses original diff owner", () => {
  assert.deepEqual(researchRecordSnapshot({goalId: "synthetic", ...input(), query: "negative", status: "refuted"}).records, [old]);
  const before = JSON.stringify(input());
  const pair = JSON.parse(exportResearchComparison(input(), "json", false));
  assert.deepEqual(pair.selected_record, old);
  assert.deepEqual(pair.comparison_record, next);
  assert.deepEqual(pair.changed_fields, changedResearchFields(old, next));
  assert.deepEqual(pair.coverage, {loaded_records: 3, exported_records: 2, history_complete: true, refresh_failed: false});
  assert.equal(pair.selected_record.measurements[0].value, null);
  assert.equal(pair.comparison_record.measurements[0].value, 0);
  assert.equal(JSON.stringify(input()), before);
  assert.deepEqual(researchComparisonSnapshot(input({comparisonId: "third"})).comparison_record, third);
});

test("invalid, removed, duplicate and cross-result identities fail instead of exporting a substitute", () => {
  for (const patch of [{comparisonId: "missing"}, {selectedId: "missing"}, {comparisonId: "old"},
    {events: [old, next, {...next}]}, {events: [old, {...next, result_id: "another"}]},
    {events: [old, {...next, event_kind: "node"}]}, {events: [old, {...next, goal_id: "other"}]},
    {events: [old, next, record({event_id: "unrelated", goal_id: "other"})]}]) {
    assert.throws(() => researchComparisonSnapshot(input(patch)));
  }
});

test("Markdown retains whole original pair, partial/stale scope and literal untrusted source prose", () => {
  const source = "```\n# Forged report\n![image](https://example.test/image)\n````\n完整原文";
  const selected = record({summary: source, title: "<img>\n# fake", run_id: "run:old"});
  const report = exportResearchComparison(input({events: [selected, next], complete: false, stale: true}), "md", false);
  assert(report.includes(`\`\`\`\`\`text\n${source}\n\`\`\`\`\``));
  assert.match(report, /Incomplete; unloaded records are not included/);
  assert.match(report, /Failed; previously read records retained/);
  assert.match(report, /not matched evaluations/);
  for (const value of ["run:old", "run:new", "synthetic:old", "synthetic:zero", '"value": 0', '"event_id": "old"', '"event_id": "next"']) assert(report.includes(value));
  assert.match(exportResearchComparison(input(), "md", true), /研究记录版本对照/);
});

test("identical field contents preserve distinct record identities without inventing differences", () => {
  const peer = {...old, event_id: "peer", recorded_at: "2026-01-03T00:00:00Z"};
  const snapshot = researchComparisonSnapshot(input({events: [old, peer], comparisonId: "peer"}));
  assert.deepEqual(snapshot.changed_fields, []);
  assert.notEqual(snapshot.selected_record.event_id, snapshot.comparison_record.event_id);
});
