import assert from "node:assert/strict";
import {test} from "node:test";
import {exportResearchRecords} from "./research-record-export.ts";

const record = (overrides = {}) => ({goal_id: "research", title: "Evidence", summary: "", blocked_reason: "", event_id: "event",
  result_id: "finding", node_id: "", parent_id: "", from_node: "", to_node: "", run_id: "",
  agent_id: "", supersedes: "", evidence_refs: [], status: "tentative", ...overrides});
const snapshot = (events, overrides = {}) => JSON.parse(exportResearchRecords({goalId: "research", events,
  query: "", status: "", complete: true, stale: false, exportedAt: "2026-01-01T00:00:00Z", ...overrides}));

test("exports every match beyond display paging with explicit coverage and literal filters", () => {
  const events = Array.from({length: 75}, (_, i) => record({event_id: `event-${i}`}));
  const result = snapshot(events, {query: "EVIDENCE", status: "tentative"});
  assert.deepEqual(result.records, events);
  assert.deepEqual(result.coverage, {loaded_records: 75, exported_records: 75, history_complete: true, refresh_failed: false});
  assert.deepEqual(result.filters, {query: "EVIDENCE", status: "tentative"});
  assert.equal(snapshot(events, {query: "Evidence.*"}).records.length, 0);
});

test("keeps exact original revisions, Unicode, multiline source and measurements without mutation", () => {
  const old = Object.freeze(record({event_id: "old", status: "refuted", summary: "质量下降\nCost unknown",
    evidence_refs: Object.freeze(["candidate:v1"]), confidence: null, measurements: [{metric_id: "accuracy", value: 0.5}]}));
  const current = Object.freeze(record({event_id: "new", status: "confirmed", supersedes: "old", evidence_refs: ["candidate:v2"]}));
  const events = Object.freeze([old, current]);
  assert.deepEqual(snapshot(events, {query: "candidate:v1", status: "refuted"}).records, [old]);
  assert.deepEqual(snapshot(events).records, [old, current]);
});

test("never equates loaded or stale records with current complete history", () => {
  for (const complete of [false, true]) for (const stale of [false, true]) {
    const result = snapshot([record()], {complete, stale});
    assert.equal(result.coverage.history_complete, complete);
    assert.equal(result.coverage.refresh_failed, stale);
    assert.equal(result.goal_id, "research");
    assert.equal(result.exported_at, "2026-01-01T00:00:00Z");
  }
});

test("rejects a mismatched Goal even when its records would be filtered out", () => {
  assert.throws(() => snapshot([record({goal_id: "other"})], {query: "no match"}), /Goal mismatch/);
  assert.throws(() => snapshot([], {goalId: ""}), /Goal mismatch/);
});
