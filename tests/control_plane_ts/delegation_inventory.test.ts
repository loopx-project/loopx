import test from "node:test";
import assert from "node:assert/strict";
import {delegationInventoryPage} from "../../loopx/control_plane/collaboration/delegation.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {PROJECTION_ENVELOPE_SERVE_REQUEST, sealProjectionEnvelope} from "../../loopx/control_plane/projection_envelope.ts";

function inventoryPageFixture() {
  const record_ids = ["a", "b", "c"].map(key => key.repeat(64));
  const observations = record_ids.slice(0, 2).map((record_id, index) => ({
    record: {record_id, operation_id: `review-${index}`},
    observation: {operation_id: `review-${index}`, request_id: `request-${index}`,
      agent_id: "reviewer", todo_id: "todo_review", status: "prepared",
      worker_active: false, recovery_required: true},
  }));
  const read_facts = {observed_at: "2026-10-01T10:00:02Z", directory_read_at: "2026-10-01T10:00:00Z",
    operations_read_at: "2026-10-01T10:00:01Z", available_count: 3};
  return {query: {limit: 2, cursor: null}, record_ids, observations, read_facts};
}

test("inventory page uses an unread lookahead without skipping or inventing results", () => {
  const request = inventoryPageFixture();
  for (const [count, more] of [[0, false], [1, false], [2, false], [3, true]] as const) {
    const page = delegationInventoryPage({...request, record_ids: request.record_ids.slice(0, count),
      observations: request.observations.slice(0, Math.min(count, 2)),
      read_facts: {...request.read_facts, available_count: count}});
    assert.deepEqual((page.items as JsonObject[]).map(row => row.record_id), request.record_ids.slice(0, Math.min(count, 2)));
    assert.equal(page.has_more, more);
    assert.equal(page.next_cursor, more ? request.record_ids[1] : null);
    assert.equal(page.page_readback_complete, true);
  }
});

test("inventory envelopes distinguish complete page, oldest read, unreadable and later replay", () => {
  const request = inventoryPageFixture();
  const page = delegationInventoryPage(request);
  const envelope = page.projection_envelope as JsonObject;
  assert.equal(envelope.complete, true);
  assert.equal(envelope.fresh, true);
  assert.deepEqual(envelope.coverage, {scope: "requester_page", expected_count: 2,
    included_count: 2, omitted: [], shown_count: 2, available_count: 3, truncated: true, complete: true});
  const replay = sealProjectionEnvelope({schema_version: PROJECTION_ENVELOPE_SERVE_REQUEST,
    envelope, served_at: "2026-10-01T10:06:00Z"});
  assert.equal(replay.observed_at, envelope.observed_at);
  assert.equal(replay.served_at, "2026-10-01T10:06:00Z");
  assert.equal(replay.fresh, false);
  assert.equal(replay.served_from_cache, true);
  assert.deepEqual(replay.alert_reasons, ["stale_sources"]);
  const slow = delegationInventoryPage({...request,
    read_facts: {...request.read_facts, observed_at: "2026-10-01T10:06:00Z"}}).projection_envelope as JsonObject;
  assert.equal(slow.fresh, false); // Assembly time cannot refresh earlier source reads.
  const damaged = delegationInventoryPage({...request, observations: [
    {...request.observations[0]!, observation: null}, request.observations[1]!],
  }).projection_envelope as JsonObject;
  assert.equal(damaged.complete, false);
  assert.deepEqual(damaged.alert_reasons, ["incomplete_coverage", "unreadable_sources"]);
});

test("malformed observations stay unavailable without hiding a healthy sibling or the next page", () => {
  const request = inventoryPageFixture();
  const invalid: (JsonObject | null)[] = [null,
    {...request.observations[0]!.observation, operation_id: "other"},
    {...request.observations[0]!.observation, status: "not-an-observation"},
    {...request.observations[0]!.observation, worker_active: null},
    {...request.observations[0]!.observation, status: "accepted", artifacts: []},
  ];
  for (const observation of invalid) {
    const page = delegationInventoryPage({...request, observations: [
      {...request.observations[0]!, observation}, request.observations[1]!,
    ]});
    const rows = page.items as JsonObject[];
    assert.equal(rows[0]!.status, "unavailable");
    assert.equal(rows[0]!.operation_id, "review-0");
    assert.equal(rows[0]!.recovery_required, null);
    assert.equal(rows[0]!.artifacts, undefined);
    assert.equal(rows[1]!.status, "prepared");
    assert.equal(page.page_readback_complete, false);
    assert.equal(page.has_more, true);
    assert.equal(page.next_cursor, request.record_ids[1]);
  }
});

test("inventory page refuses incomplete, reordered or out-of-cursor framing", () => {
  const request = inventoryPageFixture();
  for (const patch of [
    {record_ids: ["invalid"]},
    {record_ids: [request.record_ids[0]!, request.record_ids[0]!] },
    {record_ids: [...request.record_ids].reverse()},
    {record_ids: [...request.record_ids, "d".repeat(64)]},
    {query: {limit: 2, cursor: request.record_ids[0]!}},
    {observations: request.observations.slice(0, 1)},
    {observations: [...request.observations].reverse()},
    {read_facts: null},
    {read_facts: {...request.read_facts, available_count: 1}},
    {observations: [{...request.observations[0]!,
      record: {...request.observations[0]!.record, operation_id: "../other"}}, request.observations[1]!] },
  ]) assert.throws(() => delegationInventoryPage({...request, ...patch}));
});
