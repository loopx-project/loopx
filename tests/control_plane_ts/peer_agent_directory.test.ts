import assert from "node:assert/strict";
import test from "node:test";
import {projectPeerAgentDirectory} from "../../loopx/control_plane/agents/directory.ts";
import {sealProjectionEnvelope, PROJECTION_ENVELOPE_SEAL_REQUEST} from "../../loopx/control_plane/projection_envelope.ts";
const now = "2026-01-01T01:00:00Z";
type Row = Record<string, any>;
function request(count = 1): Row {
  const ids = Array.from({length: count}, (_, i) => `agent-${String(i).padStart(2, "0")}`);
  return {schema_version: "peer_agent_directory_project_request_v0", observed_at: now, goal_id: "goal",
    caller_agent_id: null, cursor: null, registered_agent_ids: ids, agents: ids.map(agent_id => ({agent_id})),
    source_summary: {selected_agent_count: count}, execution_fact_agent_ids: null,
    routes: Object.fromEntries(ids.map(id => [id, {state: "unbound", candidate_count: 0, candidates: []}])), upstream_envelopes: []};
}
function source(read_status = "read", included_count = 1): Row {
  return sealProjectionEnvelope({schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST, projection: "status",
    observed_at: "2026-01-01T00:00:00Z", sources: [{source_id: "registry", read_status,
      last_read_at: "2026-01-01T00:00:00Z", source_updated_at: "2025-12-31T23:00:00Z"}],
    coverage: {scope: "goal", expected_count: 1, included_count}});
}
test("membership uses whole registry beyond page and diagnostic claimants never gain scope", () => {
  const input = request(30); input.agents = input.agents.slice(0, 24); input.caller_agent_id = "agent-29";
  const result: Row = projectPeerAgentDirectory(input);
  assert.equal(result.scope.caller_membership, "registered_agent");
  assert.equal(result.rows.length, 24); assert.equal(result.next_cursor, "agent-23");
  assert.equal(result.omitted_row_count, 6);
  input.caller_agent_id = "claimant";
  assert.equal((projectPeerAgentDirectory(input) as Row).rows.length, 0);
  assert.equal((projectPeerAgentDirectory(input) as Row).scope.gaps[0].kind, "audience_not_authorized");
});
test("later and exhausted pages retain total omission definition", () => {
  const input = request(30); input.agents = input.agents.slice(24); input.cursor = "agent-23";
  input.source_summary.selected_agent_count = 6;
  let result: Row = projectPeerAgentDirectory(input);
  assert.equal(result.omitted_row_count, 24); assert.equal(result.next_cursor, null);
  input.agents = []; input.source_summary.selected_agent_count = 0;
  result = projectPeerAgentDirectory(input); assert.equal(result.omitted_row_count, 30);
});
test("null work, false flags, lease coverage and bounded references retain semantics", () => {
  const input = request(2); input.registered_agent_ids = ["agent-00"];
  input.source_summary.diagnostic_agent_count = 1;
  input.agents[0] = {agent_id: "agent-00", current_todo: {todo_id: "T1", status: "blocked", title: "Task"},
    execution: {lease: {expired: false}}, evidence_refs: ["a", "a", "b"], handoff_refs: ["c", "d"]};
  input.execution_fact_agent_ids = ["agent-00"];
  const result: Row = projectPeerAgentDirectory(input);
  assert.equal(result.rows[0].work.lease_expired, false); assert.equal(result.rows[0].work.claimed, false);
  assert.equal(result.rows[1].registered, false); assert.equal(result.rows[1].work, null);
  assert.deepEqual(result.rows[0].observation_refs, ["a"]); assert.deepEqual(result.rows[0].delivery_refs, ["c"]);
  assert.ok(result.limitations.includes("lease_state_not_projected"));
  input.execution_fact_agent_ids = ["agent-00", "agent-01"];
  assert.ok(!(projectPeerAgentDirectory(input) as Row).limitations.includes("lease_state_not_projected"));
  input.execution_fact_agent_ids = [];
  assert.ok((projectPeerAgentDirectory(input) as Row).limitations.includes("lease_state_not_projected"));
});
test("route cap does not erase withheld candidates or full rollup counts", () => {
  const input = request(12);
  for (const row of input.agents) row.current_todo = {todo_id: "T1", status: "blocked"};
  input.routes["agent-00"] = {state: "ambiguous", candidate_count: 7, withheld_candidate_count: 2,
    candidates: Array.from({length: 5}, (_, i) => ({thread_id: `t${i}`}))};
  const result: Row = projectPeerAgentDirectory(input);
  assert.equal(result.rows[0].peer_route.candidates.length, 3); assert.equal(result.rows[0].peer_route.candidate_count, 7);
  assert.equal(result.rollup.needs_decision.length, 8); assert.equal(result.rollup.counts.needs_decision, 12);
  assert.ok(result.limitations.includes("route_candidate_withheld"));
});
test("missing evidence is unknown, and replay preserves old source timestamps", () => {
  const input = request();
  let result: Row = projectPeerAgentDirectory(input);
  assert.equal(result.projection_envelope.fresh, false); assert.equal(result.projection_envelope.complete, false);
  assert.equal(result.projection_envelope.coverage.expected_count, null);
  input.upstream_envelopes = [source()];
  result = projectPeerAgentDirectory(input);
  assert.equal(result.projection_envelope.sources[0].last_read_at, "2026-01-01T00:00:00Z");
  assert.equal(result.projection_envelope.sources[0].source_updated_at, "2025-12-31T23:00:00Z");
  assert.equal(result.projection_envelope.fresh, false); assert.equal(result.projection_envelope.served_at, now);
  assert.ok(result.projection_envelope.alert_reasons.includes("stale_sources"));
  input.upstream_envelopes = [source("unreadable")];
  assert.ok((projectPeerAgentDirectory(input) as Row).projection_envelope.alert_reasons.includes("unreadable_sources"));
  input.upstream_envelopes = [source("read", 0)];
  assert.equal((projectPeerAgentDirectory(input) as Row).projection_envelope.complete, false);
});
test("display paging is not missing source coverage", () => {
  const input = request(30); input.upstream_envelopes = [source()];
  const result: Row = projectPeerAgentDirectory(input);
  assert.equal(result.projection_envelope.complete, true);
  assert.equal(result.projection_envelope.coverage.truncated, true);
  assert.deepEqual(result.projection_envelope.coverage.omitted, []);
});
test("strict bridge shape rejects malformed input without mutation", () => {
  for (const patch of [{schema_version: "old"}, {agents: {}}, {registered_agent_ids: [2]},
    {execution_fact_agent_ids: false}, {routes: []}, {observed_at: "not-time"},
    {upstream_envelopes: [{}]}]) assert.throws(() => projectPeerAgentDirectory({...request(), ...patch}));
  const input = request(); const before = structuredClone(input);
  projectPeerAgentDirectory(input); assert.deepEqual(input, before);
});
test("runtime dispatch reaches the single directory owner and validates nested facts", async () => {
  const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import("../../loopx/control_plane/effect_runtime_handlers.ts");
  const handlers = createEffectRuntimeHandlers({fingerprint: "test", requestShutdown: () => undefined});
  assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "agent.directory.project", request()), projectPeerAgentDirectory(request()));
  for (const row of [{agent_id: 2}, {current_todo: []}, {evidence_refs: [false]}, {execution: {lease: {expired: "false"}}}]) {
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "agent.directory.project", {...request(), agents: [row]}));
  }
});
