import assert from "node:assert/strict";
import {test} from "node:test";
import {exportResearchReport} from "./research-record-report.ts";

const record = (overrides = {}) => ({goal_id: "research", title: "Evidence", summary: "Original result",
  event_id: "event", result_id: "finding", recorded_at: "2026-01-01T00:00:00Z", status: "refuted",
  evidence_refs: [], confidence: null, ...overrides});
const input = (events, overrides = {}) => ({goalId: "research", events, query: "", status: "",
  complete: true, stale: false, exportedAt: "2026-01-02T00:00:00Z", ...overrides});

test("report preserves all matching originals beyond UI paging and the actual read scope", () => {
  const events = Object.freeze(Array.from({length: 75}, (_, i) => Object.freeze(record({title: `Observation ${i}`, event_id: `event-${i}`}))));
  const report = exportResearchReport(input(events, {complete: false, stale: true, query: "Observation", status: "refuted"}), false);
  assert.equal((report.match(/^## \d+\./gm) ?? []).length, 75);
  assert.match(report, /75 \/ 75/);
  assert.match(report, /Incomplete; unloaded records are not included/);
  assert.match(report, /Failed; previously read records retained/);
  assert.match(report, /\*\*Search:\*\* Observation/);
  assert.match(report, /\*\*Status filter:\*\* refuted/);
  assert.match(report, /Observation 74/);
});

test("translated matches keep original prose, negative revisions and measurement conditions", () => {
  const old = record({summary: "Earlier negative evidence", evidence_refs: ["result:old"], run_id: "run:old",
    measurements: [{metric: "Error", value: null, unit: "points", higher_is_better: false,
      comparison_scope: "scope:v1", input_revision: "input:v1", evaluation_ref: "method:v1", candidate_ref: "candidate:v1", evidence_ref: "result:old"}]});
  const later = record({event_id: "later", summary: "Measured zero", supersedes: "event",
    measurements: [{...old.measurements[0], value: 0}]});
  const display = {events: new Map([[old.event_id, {...old, summary: "较早的负向证据"}]])};
  const report = exportResearchReport(input([old, later], {query: "负向证据", display}), true);
  assert.match(report, /Earlier negative evidence/);
  assert.doesNotMatch(report, /Measured zero|较早的负向证据/);
  assert.match(report, /\*\*数值:\*\* 未知/);
  for (const ref of ["scope:v1", "input:v1", "method:v1", "candidate:v1", "result:old", "run:old"]) assert(report.includes(ref));
  const both = exportResearchReport(input([old, later]), false);
  assert.match(both, /\*\*Value:\*\* Unknown/);
  assert.match(both, /\*\*Value:\*\* 0/);
  assert.match(both, /\*\*Direction:\*\* Lower is better/);
  assert.match(both, /\*\*supersedes:\*\* event/);
});

test("source markup remains literal without loading remote images or forging report sections", () => {
  const summary = "```\n# Forged claim\n![remote](https://example.test/image)\n<script>bad()</script>\n````\n结论";
  const report = exportResearchReport(input([record({title: "<img>\n# title", summary,
    evidence_refs: ["[source](https://example.test)"], execution_observation: {basis: "observed"}})]), false);
  assert(report.includes(`\`\`\`\`\`text\n${summary}\n\`\`\`\`\``));
  assert.match(report, /## 1\. \\<img\\> \\# title/);
  assert(report.includes('"basis": "observed"'));
});

test("empty reports keep scope and mixed Goals are rejected before filtering", () => {
  assert.match(exportResearchReport(input([]), false), /0 \/ 0/);
  assert.throws(() => exportResearchReport(input([record({goal_id: "other"})], {query: "absent"}), false), /Goal mismatch/);
});
