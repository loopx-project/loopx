import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SOURCE_WINDOW_SECONDS,
  PROJECTION_ENVELOPE_SCHEMA_VERSION,
  PROJECTION_ENVELOPE_SEAL_REQUEST,
  PROJECTION_ENVELOPE_SERVE_REQUEST,
  sealProjectionEnvelope,
} from "../../loopx/control_plane/projection_envelope.ts";
import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";

const OBSERVED = "2026-09-26T10:00:00+00:00";
const at = (seconds: number) => new Date(Date.parse(OBSERVED) + seconds * 1000).toISOString();

function sealRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST,
    projection: "status",
    observed_at: OBSERVED,
    sources: [
      { source_id: "registry", read_status: "read", last_read_at: OBSERVED, item_count: 3 },
      { source_id: "global_registry", read_status: "missing", required: false },
    ],
    coverage: { scope: "registry", expected_count: 3, included_count: 3 },
    ...overrides,
  };
}

type Row = Record<string, unknown>;
const sources = (envelope: Row) => envelope.sources as Row[];
const coverage = (envelope: Row) => envelope.coverage as Row;

test("a live read inside its window is fresh and complete", () => {
  const envelope = sealProjectionEnvelope(sealRequest());
  assert.equal(envelope.schema_version, PROJECTION_ENVELOPE_SCHEMA_VERSION);
  assert.equal(envelope.served_at, OBSERVED);
  assert.equal(envelope.age_seconds, 0);
  assert.equal(envelope.served_from_cache, false);
  assert.equal(envelope.fresh, true);
  assert.equal(envelope.complete, true);
  assert.equal(envelope.alert, false);
  assert.deepEqual(envelope.alert_reasons, []);
  const [registry, global] = sources(envelope);
  assert.equal(registry.status, "fresh");
  assert.equal(registry.staleness_seconds, 0);
  assert.equal(registry.window_seconds, DEFAULT_SOURCE_WINDOW_SECONDS);
  assert.equal(global.status, "missing");
  assert.equal(global.alert, false, "an optional missing source is disclosed but not alerted");
});

test("staleness is measured at serve time against each source window", () => {
  const envelope = sealProjectionEnvelope(sealRequest({
    served_at: at(90),
    sources: [
      { source_id: "registry", read_status: "read", last_read_at: at(-400) },
      { source_id: "goal_run_indexes", read_status: "read", last_read_at: OBSERVED, window_seconds: 60 },
      { source_id: "contract", read_status: "read", last_read_at: OBSERVED, window_seconds: 120 },
    ],
  }));
  assert.deepEqual(sources(envelope).map((row) => [row.source_id, row.status, row.staleness_seconds]), [
    ["registry", "stale", 490],
    ["goal_run_indexes", "stale", 90],
    ["contract", "fresh", 90],
  ]);
  assert.equal(envelope.fresh, false);
  assert.deepEqual(envelope.alert_reasons, ["stale_sources"]);
  assert.deepEqual(envelope.alert_source_ids, ["registry", "goal_run_indexes"]);
});

test("unreadable, partial and required-but-missing sources alert", () => {
  const envelope = sealProjectionEnvelope(sealRequest({
    sources: [
      { source_id: "registry", read_status: "unreadable" },
      { source_id: "goal_quota", read_status: "read", last_read_at: OBSERVED, item_count: 4, unreadable_count: 1 },
      { source_id: "global_registry", read_status: "missing", required: true },
      { source_id: "rollout_events", read_status: "not_read", required: false },
    ],
  }));
  assert.deepEqual(sources(envelope).map((row) => row.alert_reasons), [
    ["unreadable"], ["partially_unreadable"], ["missing"], [],
  ]);
  assert.deepEqual(envelope.alert_reasons, ["missing_required_sources", "unreadable_sources"]);
  assert.equal(envelope.fresh, false);
});

test("coverage below the requested scope is incomplete; display truncation is disclosed separately", () => {
  const envelope = sealProjectionEnvelope(sealRequest({
    coverage: {
      scope: "global",
      expected_count: 12,
      included_count: 9,
      omitted: [{ reason: "outside_current_registry", count: 3, refs: ["a", "b", "c"] }],
      shown_count: 5,
      available_count: 9,
    },
  }));
  assert.equal(envelope.complete, false);
  assert.equal(coverage(envelope).truncated, true);
  assert.deepEqual(envelope.alert_reasons, ["incomplete_coverage"]);

  const truncatedOnly = sealProjectionEnvelope(sealRequest({
    coverage: { scope: "registry", expected_count: 3, included_count: 3, shown_count: 1, available_count: 3 },
  }));
  assert.equal(coverage(truncatedOnly).truncated, true);
  assert.equal(truncatedOnly.complete, true);
  assert.equal(truncatedOnly.alert, false, "a requested display limit is not an alert");

  const undercounted = sealProjectionEnvelope(sealRequest({
    coverage: { scope: "goal", expected_count: 1, included_count: 0 },
  }));
  assert.equal(undercounted.complete, false, "a shortfall without a named omission is still incomplete");
});

test("serving a cached copy keeps observed_at and recomputes staleness", () => {
  const sealed = sealProjectionEnvelope(sealRequest());
  const served = sealProjectionEnvelope({
    schema_version: PROJECTION_ENVELOPE_SERVE_REQUEST,
    envelope: JSON.parse(JSON.stringify(sealed)),
    served_at: at(600),
  });
  assert.equal(served.observed_at, OBSERVED);
  assert.equal(served.served_at, at(600));
  assert.equal(served.age_seconds, 600);
  assert.equal(served.served_from_cache, true);
  assert.equal(sources(served)[0].status, "stale");
  assert.deepEqual(served.alert_reasons, ["stale_sources"]);
  assert.deepEqual(coverage(served), coverage(sealed));
});

test("a derived projection inherits upstream sources and completeness", () => {
  const status = sealProjectionEnvelope(sealRequest({
    sources: [{ source_id: "registry", read_status: "read", last_read_at: at(-500) }],
    served_at: OBSERVED,
    coverage: { scope: "registry", expected_count: 3, included_count: 2 },
  }));
  const summary = sealProjectionEnvelope({
    schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST,
    projection: "global_summary",
    observed_at: at(1),
    sources: [{ source_id: "goal_quota", read_status: "read", last_read_at: at(1), item_count: 2 }],
    coverage: { scope: "global", expected_count: 2, included_count: 2 },
    upstream: [status],
  });
  assert.deepEqual(sources(summary).map((row) => [row.via ?? null, row.source_id, row.status]), [
    [null, "goal_quota", "fresh"],
    ["status", "registry", "stale"],
  ]);
  assert.deepEqual(summary.upstream, [{ projection: "status", observed_at: OBSERVED, complete: false }]);
  assert.equal(summary.complete, false, "an incomplete upstream cannot yield a complete derived projection");
  assert.deepEqual(summary.alert_source_ids, ["status/registry"]);

  const reserved = sealProjectionEnvelope({
    schema_version: PROJECTION_ENVELOPE_SERVE_REQUEST, envelope: summary, served_at: at(2),
  });
  assert.deepEqual(reserved.upstream, summary.upstream);
  assert.equal(sources(reserved)[1].via, "status");
});

test("the decoder rejects values that cannot establish freshness", () => {
  const rejects = (overrides: Record<string, unknown>, pattern: RegExp) =>
    assert.throws(() => sealProjectionEnvelope(sealRequest(overrides)), (error: unknown) =>
      error instanceof EffectRuntimeRequestError && pattern.test(error.message));
  rejects({ schema_version: "other" }, /schema mismatch/);
  rejects({ observed_at: "2026-09-26T10:00:00" }, /explicit offset/);
  rejects({ observed_at: "2026-02-30T10:00:00Z" }, /explicit offset/);
  rejects({ sources: [{ source_id: "registry", read_status: "read" }] }, /required for a read source/);
  rejects({ sources: [{ source_id: "registry", read_status: "not_read", last_read_at: OBSERVED }] }, /not_read/);
  rejects({ sources: [{ source_id: "Registry Path", read_status: "missing" }] }, /identifier/);
  rejects({ sources: [{ source_id: "registry", read_status: "fresh" }] }, /unsupported/);
  rejects({ sources: [
    { source_id: "registry", read_status: "missing" }, { source_id: "registry", read_status: "missing" },
  ] }, /duplicate source/);
  rejects({ sources: [{ source_id: "registry", read_status: "missing", window_seconds: 0 }] }, /positive/);
  rejects({ coverage: { scope: "registry", included_count: -1 } }, /non-negative/);
  rejects({ coverage: { scope: "registry", included_count: 1, omitted: [{ reason: "x", count: 0 }] } }, /positive/);
  rejects({ sources: Array.from({ length: 33 }, (_, index) => ({ source_id: `s${index}`, read_status: "missing" })) },
    /exceeds 32/);
  assert.throws(() => sealProjectionEnvelope({
    schema_version: PROJECTION_ENVELOPE_SERVE_REQUEST, envelope: { schema_version: "other" }, served_at: OBSERVED,
  }), /schema mismatch/);
});

test("unknown coverage stays incomplete through replay and upstream composition", () => {
  const unknown = sealProjectionEnvelope(sealRequest({
    coverage: { scope: "global", expected_count: null, included_count: 1 },
  }));
  assert.equal(unknown.complete, false);
  assert.deepEqual(unknown.alert_reasons, ["incomplete_coverage"]);
  const replay = sealProjectionEnvelope({ schema_version: PROJECTION_ENVELOPE_SERVE_REQUEST,
    envelope: unknown, served_at: at(1) });
  assert.equal(replay.complete, false);
  const derived = sealProjectionEnvelope(sealRequest({ projection: "global_gates", upstream: [unknown] }));
  assert.equal(derived.complete, false);
});
