import assert from "node:assert/strict";
import test from "node:test";
import { selectObservedPeerHostRoute } from "../../loopx/control_plane/collaboration/peer_host_route.ts";

const archived = { state: "archived" };
const idle = { state: "idle" };
const open = { state: "turn_open" };
const unknown = { state: "unavailable", reason: "thread_not_found" };
const resolved = (index: number) => ({ status: "resolved", reason: null, selected_index: index });
const ambiguous = { status: "ambiguous", reason: "multiple_binding_candidates", selected_index: null };
const absent = { status: "unavailable", reason: "host_thread_archived", selected_index: null };

test("one readable task survives only positively archived alternatives, in either order", () => {
  for (const [observations, expected] of [
    [[idle], resolved(0)], [[open], resolved(0)], [[archived], absent],
    [[archived, idle], resolved(1)], [[open, archived], resolved(0)],
    [[archived, archived, open], resolved(2)], [[archived, archived], absent],
    [[idle, open], ambiguous], [[unknown, idle], ambiguous],
    [[idle, archived, unknown], ambiguous], [[unknown, unknown], ambiguous],
    [[archived, unknown], { status: "unavailable", reason: "thread_not_found", selected_index: null }],
  ] as const) {
    assert.deepEqual(selectObservedPeerHostRoute({ observations: [...observations] }), expected);
  }
});

test("unsupported, failed and withheld alternatives retain uncertainty", () => {
  for (const reason of ["host_observer_unavailable", "host_observation_failed", "route_candidate_withheld",
    "store_unavailable", "record_unrecognized", "no_turn_marker", "unsupported_host"]) {
    const unavailable = { state: "unavailable", reason };
    assert.deepEqual(selectObservedPeerHostRoute({ observations: [idle, unavailable] }), ambiguous);
    assert.deepEqual(selectObservedPeerHostRoute({ observations: [archived, unavailable] }), {
      status: "unavailable", reason, selected_index: null,
    });
  }
});

test("malformed or over-budget observations cannot choose a route", () => {
  for (const observations of [[], Array(33).fill(archived), [{ state: "ready" }],
    [{ state: "unavailable" }], [{ state: "unavailable", reason: "assume_archived" }]]) {
    assert.throws(() => selectObservedPeerHostRoute({ observations }));
  }
  assert.deepEqual(selectObservedPeerHostRoute({ observations: [...Array(31).fill(archived), idle] }), resolved(31));
});
