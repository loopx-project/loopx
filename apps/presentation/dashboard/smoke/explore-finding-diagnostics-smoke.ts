import assert from "node:assert/strict";
import {fetchExploreResults} from "../src/data/chat";

// The saved-finding transport used to strip the backend's quarantine reason.
// A measured, quarantined and unmeasured finding must remain distinguishable.
const row = {finding_id: "legacy", finding: "Recorded finding", summary: "Original observation",
  status: "confirmed", node_id: "question", question: "Compare candidates", scope: "Fixed input",
  agent_id: "recorder", evidence_refs: ["result:original"], last_updated_at: "2026-01-01T00:00:00Z",
  linked_todos: []};
const measurement = {metric: "quality", value: 8, unit: "points", higher_is_better: true,
  comparison_scope: "validation:fixed", input_revision: "input:v1", evaluation_ref: "method:v1",
  candidate_ref: "candidate:a", evidence_ref: "result:original"};
const proxyProfile = {...measurement, metric: "proxy_score", evaluation_ref: "method:proxy-v1"};
delete (proxyProfile as Record<string, unknown>).value;
delete (proxyProfile as Record<string, unknown>).candidate_ref;
delete (proxyProfile as Record<string, unknown>).evidence_ref;
const validFidelity = {
  schema_version: "scoped_yardstick_fidelity_v0",
  node_id: "question",
  authoritative: {metric: "quality", unit: "points", higher_is_better: true,
    comparison_scope: "validation:fixed", input_revision: "input:v1", evaluation_ref: "method:v1"},
  proxy: proxyProfile,
  fidelity_status: "frontier_bias_cliff",
  calibration_tier: "multi_point",
  counts: {paired_candidates: 4, authoritative_only_candidates: 0, proxy_only_candidates: 1,
    authoritative_unknowns: 0, proxy_unknowns: 0, repeat_observations: 0},
  rank_concordance: {concordant_pairs: 4, discordant_pairs: 2, authoritative_ties: 0,
    proxy_ties: 0, joint_ties: 0, kendall_tau_b: 0.333333, spearman_rho: 0.4},
  linear_calibration: {utility_slope: 0.5, utility_intercept: 1, raw_slope: 0.5,
    raw_intercept: 1, residual_mad: 0.2, r_squared: 0.3},
  frontier_neighborhood: {frontier_k: 3, concordant_pairs: 0, discordant_pairs: 3,
    frontier_kendall_tau_b: -1, authoritative_best_candidate_ref: "candidate:d",
    proxy_best_paired_candidate_ref: "candidate:b", best_candidate_agrees: false, bias_cliff_detected: true},
  audit_candidates: {unconfirmed_proxy_winners: [], proxy_false_negatives: []},
  paired_points: [],
  omitted_paired_points: 4,
  scope_bound: true,
  global_evaluator_blacklist: false,
  advisory_only: true,
  grants_execution_authority: false,
};
const reason = "measurements must cite the executed candidate's result reference";
const previousFetch = globalThis.fetch;
try {
  globalThis.fetch = async () => new Response(JSON.stringify({ok: true, goal_id: "research", total: 3,
    next_cursor: null, items: [{...row, measurement_error: reason},
      {...row, finding_id: "measured", measurements: [measurement], yardstick_fidelity: validFidelity},
      {...row, finding_id: "unmeasured"}]}),
    {headers: {"Content-Type": "application/json"}});
  const page = await fetchExploreResults("research");
  assert.equal(page.items[0]!.measurement_error, reason, "Quarantine reason reaches the existing reader");
  assert.equal(page.items[0]!.measurements, undefined, "Quarantined quantities stay absent");
  assert.equal(page.items[0]!.status, "confirmed", "The transport does not rewrite the recorded finding status");
  assert.equal(page.items[1]!.measurements?.[0]?.value, 8, "Valid quantities retain their value");
  assert.equal(page.items[1]!.yardstick_fidelity?.fidelity_status, "frontier_bias_cliff");
  assert.equal(page.items[1]!.measurement_error, undefined);
  assert.equal(page.items[2]!.measurements, undefined);
  assert.equal(page.items[2]!.measurement_error, undefined, "Unmeasured is not a quarantine error");

  globalThis.fetch = async () => new Response(JSON.stringify({ok: true, goal_id: "research", total: 1,
    next_cursor: null, items: [{...row, yardstick_fidelity: {...validFidelity, should_run: true}}]}),
    {headers: {"Content-Type": "application/json"}});
  await assert.rejects(() => fetchExploreResults("research"), /scoped_yardstick_fidelity_v0/);
} finally {
  globalThis.fetch = previousFetch;
}
console.log("PASS saved finding measurement diagnostics, valid values and unmeasured parity");
