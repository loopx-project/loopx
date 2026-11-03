import assert from "node:assert/strict";
import test from "node:test";
import {aggregateGlobalRisks, projectGlobalRisks} from "../../loopx/control_plane/presentation/global_risk_read.ts";
import {sealProjectionEnvelope} from "../../loopx/control_plane/projection_envelope.ts";

const NOW = "2026-11-02T12:00:00+00:00";
const empty = () => ({ok: true, contract: {error_diagnostics: []},
  global_registry: {available: true, findings: [], global_goal_count: 1}, attention_queue: {items: []}});
const input = () => ({schema_version: "loopx_global_risks_read_v0", observed_at: NOW,
  limit: 8, agent_id: null, time_range: "24h", status: empty(), host_poll: {read_status: "missing"}});

test("aggregation preserves occurrence identity, raises severity and unions evidence in source order", () => {
  const first = {goal_id: "goal-a", category: "failing_check", kind: "synthetic", severity: "warning",
    occurrence_id: "0123456789abcdef", occurrence_count: 1, evidence_refs: ["first"]};
  const rows = aggregateGlobalRisks([first, {...first, severity: "high", evidence_refs: ["second", "first"]},
    {...first, occurrence_id: "fedcba9876543210", occurrence_count: false}]);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {...first, severity: "high", occurrence_count: 2, evidence_refs: ["first", "second"]});
  assert.equal(rows[1].occurrence_count, 1);
  assert.equal(first.severity, "warning");
});

test("stale poll decisions require continuation and compare microsecond instants inclusively", () => {
  for (const [stamp, gate, expected] of [
    ["2026-11-02T08:00:00Z", true, 1], ["2026-11-02T08:00:00.000001Z", true, 0],
    ["2026-11-02T16:00:00+08:00", true, 1], ["2026-11-02T08:00:00", true, 1],
    ["invalid", true, 0], ["2026-11-01T00:00:00Z", false, 0], ["2026-11-01T00:00:00Z", null, 0],
  ]) {
    const result = projectGlobalRisks({...input(), host_poll: {read_status: "read", goal_count: 1,
      goals: [{goal: {id: "goal-a"}, observation: {read_status: "read", receipt: {
        expected_continuation: gate, last_poll_at: stamp}}}]}});
    assert.equal((result.risks as unknown[]).length, expected, String(stamp));
  }
});

test("boundary rejects malformed request carriers before projection", () => {
  for (const replacement of [{schema_version: "wrong"}, {observed_at: "invalid"}, {limit: true},
    {agent_id: []}, {time_range: 24}, {host_poll: null}]) {
    assert.throws(() => projectGlobalRisks({...input(), ...replacement}));
  }
});

test("upstream stale copy, unreadable receipts and omitted scope remain visible in the envelope", () => {
  const upstream = sealProjectionEnvelope({schema_version: "loopx_projection_envelope_seal_request_v0", projection: "status",
    observed_at: "2026-11-02T10:00:00+00:00", sources: [{source_id: "global_registry", read_status: "read",
      last_read_at: "2026-11-02T10:00:00+00:00"}], coverage: {scope: "global", expected_count: 1, included_count: 1}});
  const result = projectGlobalRisks({...input(), status: {...empty(), projection_envelope: upstream},
    host_poll: {read_status: "read", goal_count: 41, goals: [{goal: {id: "goal-a"}, observation: {read_status: "unreadable"}}]}});
  const envelope = result.projection_envelope as Record<string, unknown>;
  assert.equal(envelope.alert, true);
  const coverage = envelope.coverage as Record<string, unknown>;
  assert.equal(coverage.complete, false);
  assert.match(JSON.stringify(envelope), /host_poll_receipts_unreadable/);
  assert.match(JSON.stringify(envelope), /host_poll_goals_not_scanned/);
  const replay = sealProjectionEnvelope({schema_version: "loopx_projection_envelope_serve_request_v0", envelope,
    served_at: "2026-11-02T13:00:00+00:00"});
  assert.equal(replay.observed_at, NOW);
  assert.equal(replay.served_from_cache, true);
});

test("agent scope failure returns an error without certifying a successful filtered read", () => {
  const result = projectGlobalRisks({...input(), agent_id: "agent-a", status: {...empty(),
    contract: {error_diagnostics: [{code: "synthetic", scope: "goal", goal_id: "goal-a"}]}}});
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "agent_scope_unavailable");
  assert.equal(result.groups, undefined);
});
