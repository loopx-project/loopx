import assert from "node:assert/strict";
import {test} from "node:test";
import {searchResearchRecords} from "./research-record-search.ts";

const record = (overrides = {}) => ({title: "", summary: "", blocked_reason: "", event_id: "event",
  result_id: "finding", node_id: "", parent_id: "", from_node: "", to_node: "", run_id: "",
  agent_id: "", supersedes: "", evidence_refs: [], status: "tentative", ...overrides});

test("searches full records before the caller's display limit and preserves source order", () => {
  const records = Array.from({length: 75}, (_, i) => record({event_id: `event-${i}`, summary: i === 74 ? "Negative result for candidate Z" : "Other evidence"}));
  assert.deepEqual(searchResearchRecords(records, "NEGATIVE z").map(row => row.event_id), ["event-74"]);
  assert.equal(searchResearchRecords(records, "  ").length, 75);
});

test("finds exact old source versions without substituting the latest finding", () => {
  const old = record({event_id: "old", summary: "Negative result", status: "refuted", evidence_refs: ["candidate:abc.123"]});
  const current = record({event_id: "new", summary: "New scope", status: "confirmed", supersedes: "old"});
  assert.deepEqual(searchResearchRecords([old, current], "candidate:abc.123", "refuted"), [old]);
  assert.deepEqual(searchResearchRecords([old, current], "candidate:abc.123", "confirmed"), []);
  assert.deepEqual(searchResearchRecords([old, current], "abc.*"), [], "Search terms are literal, not regular expressions");
  assert.deepEqual(searchResearchRecords([old, current], "", "confirmed"), [current]);
});

test("terms may span provenance and original multilingual text without mutating records", () => {
  const source = Object.freeze(record({summary: "质量下降；成本未知", run_id: "run:42", agent_id: "researcher", evidence_refs: Object.freeze(["input:deadbeef"])}));
  assert.deepEqual(searchResearchRecords([source], "质量下降 RUN:42 deadbeef"), [source]);
  assert.deepEqual(searchResearchRecords([source], "quality"), [], "The search does not translate or infer source claims");
});
