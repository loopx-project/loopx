import assert from "node:assert/strict";
import test from "node:test";

import { buildSeenMarker, compareSeenMarker, readSeenMarker, seenMarkerIdLimit, seenMarkerStorage, seenMarkerStorageKey, writeSeenMarker } from "./since-you-looked.ts";

const input = (overrides = {}) => ({ observedAt: "2026-10-15T08:00:00Z", counts: { open: 5, blocked: 1, done: 20, decisions: 1 }, decisionIds: ["d1"], doneTodoIds: ["a", "b"], blockedTodoIds: ["x"], resultIds: ["r1"], ...overrides });

test("a marker keeps typed counts and ids only, bounded", () => {
  const marker = buildSeenMarker(input({ doneTodoIds: Array.from({ length: seenMarkerIdLimit + 50 }, (_, i) => `t${i}`) }));
  assert.equal(marker.schema, "goal_seen_marker_v1");
  assert.equal(marker.doneTodoIds.length, seenMarkerIdLimit, "done ids are bounded to the newest");
  assert.equal(marker.doneTodoIds[0], "t50");
  assert.deepEqual(Object.keys(marker).sort(), ["counts", "decisionIds", "doneTodoIds", "observedAt", "resultIds", "schema"], "no titles, text or cost");
});

test("a no-change pair stays silent", () => {
  const marker = buildSeenMarker(input());
  const delta = compareSeenMarker(marker, input({ observedAt: "2026-10-15T09:00:00Z" }));
  assert.equal(delta.material, false);
  assert.deepEqual([delta.newlyDone, delta.newDecisions, delta.newlyBlocked, delta.resultsReturned], [0, 0, 0, 0]);
  assert.equal(delta.since, "2026-10-15T08:00:00Z");
});

test("known work, decision, blocked and result changes are counted since the marker", () => {
  const marker = buildSeenMarker(input());
  const delta = compareSeenMarker(marker, input({ counts: { open: 4, blocked: 2, done: 22, decisions: 2 }, decisionIds: ["d1", "d2"], doneTodoIds: ["a", "b", "c", "d"], resultIds: ["r1", "r2"] }));
  assert.deepEqual([delta.newlyDone, delta.newDecisions, delta.newlyBlocked, delta.resultsReturned], [2, 1, 1, 1]);
  assert.equal(delta.material, true);
});

test("a shrinking queue or a resolved decision is not reported as new", () => {
  const marker = buildSeenMarker(input({ decisionIds: ["d1", "d2"], counts: { open: 5, blocked: 3, done: 20, decisions: 2 } }));
  const delta = compareSeenMarker(marker, input({ decisionIds: [], counts: { open: 5, blocked: 1, done: 20, decisions: 0 } }));
  assert.equal(delta.material, false);
});

test("a trimmed marker cannot inflate newly done beyond the done-count growth", () => {
  const many = Array.from({ length: seenMarkerIdLimit + 10 }, (_, i) => `t${i}`);
  const marker = buildSeenMarker(input({ doneTodoIds: many, counts: { open: 5, blocked: 0, done: many.length, decisions: 1 } }));
  const delta = compareSeenMarker(marker, input({ doneTodoIds: [...many, "new"], counts: { open: 4, blocked: 0, done: many.length + 1, decisions: 1 } }));
  assert.equal(delta.newlyDone, 1, "the ten ids trimmed from the marker are old work, not new");
});

test("without a projected done count the ids alone decide", () => {
  const marker = buildSeenMarker(input({ counts: { open: 5, blocked: 0, done: null, decisions: 1 } }));
  const delta = compareSeenMarker(marker, input({ counts: { open: 5, blocked: 0, done: null, decisions: 1 }, doneTodoIds: ["a", "b", "c"] }));
  assert.equal(delta.newlyDone, 1);
});

test("storage round-trips per source and Goal and tolerates garbage", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => { store.set(k, v); } };
  const key = seenMarkerStorageKey("/status.json", "g1");
  assert.notEqual(key, seenMarkerStorageKey("http://remote/status.json", "g1"), "sources keep separate markers");
  const marker = buildSeenMarker(input());
  assert.equal(writeSeenMarker(storage, key, marker), true);
  assert.deepEqual(readSeenMarker(storage, key), marker);
  store.set(key, "{not json");
  assert.equal(readSeenMarker(storage, key), null);
  store.set(key, JSON.stringify({ schema: "other" }));
  assert.equal(readSeenMarker(storage, key), null);
  const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.equal(readSeenMarker(throwing, key), null);
  assert.equal(writeSeenMarker(throwing, key, marker), false);
});

test("a browser that denies storage leaves the marker silent, whether the property or its methods throw", () => {
  const key = seenMarkerStorageKey("/status.json", "g1");
  const denied = {};
  Object.defineProperty(denied, "localStorage", { get() { throw new Error("SecurityError: storage denied"); } });
  assert.equal(seenMarkerStorage(denied), null, "a throwing localStorage getter is caught at acquisition");
  assert.equal(seenMarkerStorage({}), null, "an absent storage is null, not a crash");
  assert.equal(seenMarkerStorage({ localStorage: undefined }), null);
  assert.equal(readSeenMarker(null, key), null);
  assert.equal(writeSeenMarker(null, key, buildSeenMarker(input())), false);
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => { store.set(k, v); } };
  assert.equal(seenMarkerStorage({ localStorage: storage }), storage, "a usable storage passes through unchanged");
});

test("past the id cap an unchanged decision set is silent and only count growth is new", () => {
  const ids = Array.from({ length: seenMarkerIdLimit + 1 }, (_, i) => `g${i}`);
  const marker = buildSeenMarker(input({ decisionIds: ids, counts: { open: 5, blocked: 0, done: 0, decisions: ids.length } }));
  assert.equal(marker.decisionIds.length, seenMarkerIdLimit);
  assert.equal(compareSeenMarker(marker, input({ decisionIds: ids, counts: { open: 5, blocked: 0, done: 0, decisions: ids.length } })).newDecisions, 0, "201 unchanged decisions are not one new decision");
  assert.equal(compareSeenMarker(marker, input({ decisionIds: [...ids.slice(1), "g0"], counts: { open: 5, blocked: 0, done: 0, decisions: ids.length } })).newDecisions, 0, "a reordered window is not a change");
  assert.equal(compareSeenMarker(marker, input({ decisionIds: [...ids, "g-new"], counts: { open: 5, blocked: 0, done: 0, decisions: ids.length + 1 } })).newDecisions, 1, "a real new decision shows as the count growth");
  const partial = buildSeenMarker(input({ decisionIds: ["d1"], counts: { open: 5, blocked: 0, done: 0, decisions: 3 } }));
  assert.equal(compareSeenMarker(partial, input({ decisionIds: ["d1", "d2"], counts: { open: 5, blocked: 0, done: 0, decisions: 3 } })).newDecisions, 0, "a partially listed set that merely lists more is not new");
});

test("within the cap a resolved-and-raised decision is still one new decision", () => {
  const marker = buildSeenMarker(input({ decisionIds: ["d1", "d2"], counts: { open: 5, blocked: 0, done: 0, decisions: 2 } }));
  assert.equal(compareSeenMarker(marker, input({ decisionIds: ["d2", "d3"], counts: { open: 5, blocked: 0, done: 0, decisions: 2 } })).newDecisions, 1);
});

test("results past the cap follow the same rule", () => {
  const ids = Array.from({ length: seenMarkerIdLimit + 1 }, (_, i) => `r${i}`);
  const marker = buildSeenMarker(input({ resultIds: ids }));
  assert.equal(marker.counts.results, ids.length);
  assert.equal(compareSeenMarker(marker, input({ resultIds: ids })).resultsReturned, 0);
  assert.equal(compareSeenMarker(marker, input({ resultIds: [...ids, "r-new"] })).resultsReturned, 1);
});

test("with no done total a capped done history proves nothing; within the cap the ids decide", () => {
  const ids = Array.from({ length: seenMarkerIdLimit + 1 }, (_, i) => `done${i}`);
  const marker = buildSeenMarker(input({ doneTodoIds: ids, counts: { open: 5, blocked: 0, done: null, decisions: 1 } }));
  assert.equal(marker.doneTodoIds.length, seenMarkerIdLimit);
  assert.equal(compareSeenMarker(marker, input({ doneTodoIds: ids, counts: { open: 5, blocked: 0, done: null, decisions: 1 } })).newlyDone, 0, "201 unchanged completions with no total are not one new completion");
  assert.equal(compareSeenMarker(marker, input({ doneTodoIds: [...ids, "done-new"], counts: { open: 5, blocked: 0, done: null, decisions: 1 } })).newlyDone, 0, "past the cap with no total there is no evidence either way");
  const within = buildSeenMarker(input({ doneTodoIds: ["a", "b"], counts: { open: 5, blocked: 0, done: null, decisions: 1 } }));
  assert.equal(compareSeenMarker(within, input({ doneTodoIds: ["a", "b", "c"], counts: { open: 5, blocked: 0, done: null, decisions: 1 } })).newlyDone, 1, "a complete history still counts a real completion");
  assert.equal(compareSeenMarker(marker, input({ doneTodoIds: [...ids, "done-new"], counts: { open: 5, blocked: 0, done: 202, decisions: 1 } })).newlyDone, 0, "a total that appears later cannot be compared with a marker that had none");
});
