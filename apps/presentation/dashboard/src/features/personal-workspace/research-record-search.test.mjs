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

test("matches the reader's translated narrative alongside original text and identity, returning only originals", () => {
  const original = Object.freeze(record({title: "Negative observation", summary: "Original serialized finding", event_id: "old",
    status: "refuted", run_id: "run:old", execution_observation: {change_reason: "Keep the reference"}}));
  const other = Object.freeze(record({event_id: "new", title: "Later evidence", status: "confirmed"}));
  const events = Object.freeze([original, other]);
  const display = {events: new Map([["old", {...original, title: "负向观察", execution_observation: {change_reason: "保留参考方案"}}]]),
    sections: new Map([["old", {applicability: "仅限本地", observation: "完整成本未知", interpretation: "",
      decision: {outcome: "replan", changed: ["核对原始证据"], stopped: ["停止当前路线"], retained: ["保留旧版本"]}}]])};
  for (const text of ["负向观察", "仅限本地", "完整成本未知", "核对原始证据", "停止当前路线", "保留旧版本", "保留参考方案"]) {
    assert.deepEqual(searchResearchRecords(events, `${text} RUN:OLD`, "refuted", display), [original]);
  }
  assert.deepEqual(searchResearchRecords(events, "负向观察 Negative", "", display), [original]);
  assert.equal(searchResearchRecords(events, "负向观察", "", display)[0], original);
  assert.deepEqual(searchResearchRecords(events, "负向观察", "confirmed", display), []);
  assert.deepEqual(searchResearchRecords(events, "Later", "", display), [other], "Partial translations retain original matching");
  assert.deepEqual(searchResearchRecords(events, "负向观察"), [], "An absent/stale display never supplies cached aliases");
  assert.equal(original.title, "Negative observation");
});
