import assert from "node:assert/strict";
import {test} from "node:test";
import {searchResearchRecords, researchRecordGroups, readResearchRecordsLocation, writeResearchRecordsLocation,
  defaultResearchRecordsLocation} from "./research-record-search.ts";
import {projectExploreHistory} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";

const record = (overrides = {}) => ({title: "", summary: "", blocked_reason: "", event_id: "event",
  result_id: "finding", node_id: "", parent_id: "", from_node: "", to_node: "", run_id: "",
  agent_id: "", supersedes: "", evidence_refs: [], status: "tentative", ...overrides});

test("record bookmarks retain filters, later-page expansion and unrelated research focus only for their original scope", () => {
  const state = {open: true, query: "negative 原文", status: "refuted", expanded: ["node:question-57", "event:unlinked"], limit: 100};
  const scope = JSON.stringify(["goal-a", "original-goal", "/status.json?source_namespace=source-a"]);
  const search = new URLSearchParams("research_event=old-version&research_node=question-57");
  const legacy = writeResearchRecordsLocation(search, scope, state);
  assert.deepEqual(readResearchRecordsLocation(legacy, scope), state);
  const focused = {...state, focused: "event:unlinked"};
  const saved = writeResearchRecordsLocation(search, scope, focused);
  assert.deepEqual(readResearchRecordsLocation(new URLSearchParams(saved.toString()), scope), focused);
  assert.equal(saved.get("research_event"), "old-version");
  assert.equal(search.has("research_records"), false, "Writing a bookmark must not mutate another reader's URL");
  for (const other of ["", scope.replace("goal-a", "goal-b"), scope.replace("source-a", "source-b")]) {
    assert.deepEqual(readResearchRecordsLocation(saved, other), defaultResearchRecordsLocation);
  }
});

test("old, ambiguous and malformed bookmarks recover to closed records without selecting a substitute", () => {
  const valid = {scope: "scope", open: true, query: "", status: "", expanded: ["missing-event"], limit: 50};
  for (const input of ["", "research_records=broken", "research_records=null", "research_records=[]",
    ...[{...valid, expanded: [42]}, {...valid, open: "true"}, {...valid, query: []}, {...valid, limit: 49}, {...valid, limit: 50.5}]
      .map(value => new URLSearchParams({research_records: JSON.stringify(value)}).toString())]) {
    assert.deepEqual(readResearchRecordsLocation(new URLSearchParams(input), "scope"), defaultResearchRecordsLocation);
  }
  const duplicate = writeResearchRecordsLocation(new URLSearchParams(), "scope", valid);
  duplicate.append("research_records", duplicate.get("research_records"));
  assert.deepEqual(readResearchRecordsLocation(duplicate, "scope"), defaultResearchRecordsLocation);
  const missing = writeResearchRecordsLocation(new URLSearchParams(), "scope", valid);
  assert.deepEqual(readResearchRecordsLocation(missing, "scope").expanded, ["missing-event"], "The reader reports an absent selection instead of choosing the latest event");
});

test("linked lifecycle and conflicting finding revisions retain every original record, never merge on title or time", () => {
  const node = record({goal_id: "goal", event_kind: "node", event_id: "node-event", result_id: "q", status: "resolved"});
  const old = record({goal_id: "goal", event_kind: "finding", event_id: "old", result_id: "f", node_id: "q", status: "confirmed"});
  const current = {...old, event_id: "new", status: "refuted", supersedes: "old"};
  const other = {...node, event_id: "other", result_id: "other-q"};
  const records = [node, old, current, other];
  const groups = researchRecordGroups(records, projectExploreHistory(records, "goal").nodes);
  assert.deepEqual(groups.map(group => group.events.map(event => event.event_id)), [["node-event", "old", "new"], ["other"]]);
  const conflictingRun = records.map(event => ({...event, run_id: event.event_id === "new" ? "run-b" : "run-a"}));
  assert.equal(researchRecordGroups(conflictingRun, projectExploreHistory(conflictingRun, "goal").nodes).length, 4);
  const unlinked = records.map(event => ({...event, node_id: ""}));
  assert.equal(researchRecordGroups(unlinked, projectExploreHistory(unlinked, "goal").nodes).length, 4);
});

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

test("UTC recorded-day range includes both days, intersects status/text and excludes unknown timestamps", () => {
  const records = [
    record({event_id: "before", recorded_at: "2026-01-01T23:59:59.999Z"}),
    record({event_id: "start", recorded_at: "2026-01-02T00:00:00Z", summary: "target"}),
    record({event_id: "end", recorded_at: "2026-01-03T23:59:59.999Z", summary: "target"}),
    record({event_id: "offset", recorded_at: "2026-01-04T01:00:00+02:00", summary: "target"}),
    record({event_id: "after", recorded_at: "2026-01-04T00:00:00Z"}),
    record({event_id: "unknown", recorded_at: "unknown"}),
    record({event_id: "unqualified-clock", recorded_at: "2026-01-03T12:00:00"}),
  ];
  const range = {from: "2026-01-02", to: "2026-01-03"};
  assert.deepEqual(searchResearchRecords(records, "target", "tentative", undefined, range).map(row => row.event_id), ["start", "end", "offset"]);
  assert.equal(searchResearchRecords(records, "", "", undefined, {from: "2026-01-04", to: "2026-01-02"}).length, 0);
  assert.equal(searchResearchRecords(records, "", "", undefined, {from: "2026-02-30"}).length, 0);
  assert.equal(searchResearchRecords(records, "").length, records.length);
  assert.deepEqual(searchResearchRecords(records, "", "", undefined, {to: "2026-01-01"}).map(row => row.event_id), ["before"]);
});

test("date bookmarks stay scoped and preserve reversed bounds for visible correction", () => {
  const state = {...defaultResearchRecordsLocation, open: true, from: "2026-02-02", to: "2026-02-01"};
  const url = writeResearchRecordsLocation(new URLSearchParams(), "source-goal", state);
  assert.deepEqual(readResearchRecordsLocation(url, "source-goal"), state);
  assert.deepEqual(readResearchRecordsLocation(url, "other-source"), defaultResearchRecordsLocation);
});

test("recorded instants require a real source calendar and clock before UTC conversion", () => {
  const invalid = ["1900-02-29T16:00:00Z", "2026-02-30T16:00:00Z", "2025-02-29T23:00:00-01:00", "2026-04-31T12:00:00Z",
    "2026-03-01T24:00:00Z", "2026-03-02T12:60:00Z", "2026-03-02T12:00:60Z",
    "2026-03-02T12:00:00+24:00", "2026-03-02T12:00:00+02:60", "March 2, 2026 12:00:00 GMTZ"];
  const rows = invalid.map((recorded_at, index) => record({event_id: `invalid-${index}`, recorded_at}));
  const valid = record({event_id: "offset", recorded_at: "2026-03-03T01:00:00+02:00"});
  assert.deepEqual(searchResearchRecords([...rows, valid], "", "", undefined, {from: "1900-01-01", to: "2026-12-31"}), [valid]);
  assert.deepEqual(searchResearchRecords([...rows, valid], ""), [...rows, valid], "Unfiltered reading retains original malformed records");
  const centuryLeap = record({recorded_at: "2000-02-29T12:00:00Z"});
  assert.deepEqual(searchResearchRecords([centuryLeap], "", "", undefined, {from: "2000-02-29", to: "2000-02-29"}), [centuryLeap]);
  const leap = record({recorded_at: "2024-02-29T23:30:00-01:00"});
  assert.deepEqual(searchResearchRecords([leap], "", "", undefined, {from: "2024-03-01", to: "2024-03-01"}), [leap]);
  assert.deepEqual(searchResearchRecords([leap], "", "", undefined, {to: "2024-02-29"}), []);
});
