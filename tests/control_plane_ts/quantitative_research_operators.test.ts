import assert from "node:assert/strict";
import test from "node:test";
import {evaluateScopedYardstickFidelity} from "../../loopx/control_plane/capabilities/yardstick_fidelity.ts";
import {evaluateBudgetedContinuationPlan} from "../../loopx/control_plane/capabilities/budgeted_continuation.ts";
import {evaluateCounterfactualContinuations} from "../../loopx/control_plane/capabilities/counterfactual_continuation.ts";

const authMax = {
  metric: "heldout_score",
  unit: "points",
  higher_is_better: true,
  comparison_scope: "bench:v1",
  input_revision: "rev-1",
  evaluation_ref: "eval:authoritative-v1",
};
const proxyMin = {
  metric: "val_loss",
  unit: "nats",
  higher_is_better: false,
  comparison_scope: "bench:v1",
  input_revision: "rev-1",
  evaluation_ref: "eval:proxy-v1",
};

function makeFinding(
  id: string,
  order: number,
  cand: string,
  authVal: number | null | undefined,
  proxyVal: number | null | undefined,
  extra: Record<string, unknown> = {},
) {
  const measurements = [];
  if (authVal !== undefined) {
    measurements.push({...authMax, candidate_ref: cand, evidence_ref: `ev:${id}:a`, value: authVal});
  }
  if (proxyVal !== undefined) {
    measurements.push({...proxyMin, candidate_ref: cand, evidence_ref: `ev:${id}:p`, value: proxyVal});
  }
  return {
    finding_id: id,
    node_id: "node-opt",
    status: "confirmed",
    recorded_order: order,
    measurements,
    ...extra,
  };
}

test("ScopedYardstickFidelity: detects Goodhart frontier_bias_cliff across opposite-direction metrics and audits unconfirmed proxy winners", () => {
  // 6 paired candidates:
  // Candidates c1..c3 (bottom half): proxy and authoritative strongly agree.
  // Candidates c4..c6 (top-3 authoritative frontier): proxy reverses order (lower val_loss picks c4 over c6, while c6 has highest heldout_score)!
  const findings = [
    makeFinding("f1", 1, "cand:c1", 10, 2.50),
    makeFinding("f2", 2, "cand:c2", 20, 2.10),
    makeFinding("f3", 3, "cand:c3", 30, 1.80),
    makeFinding("f4", 4, "cand:c4", 70, 0.85),
    makeFinding("f5", 5, "cand:c5", 80, 0.95),
    makeFinding("f6", 6, "cand:c6", 92, 1.15),
    // Unconfirmed proxy winner (val_loss 0.70 beats best paired 0.85, no authoritative score yet)
    makeFinding("f7", 7, "cand:unconfirmed_star", undefined, 0.70),
    // Null unknown measurement: counted as unknown, never coerced to 0
    makeFinding("f8", 8, "cand:unknown", null, 1.40),
    // Quarantined finding with mismatched execution_observation.input_revision: ignored!
    makeFinding("f9", 9, "cand:spoof", 999, 0.10, {
      execution_observation: {
        planned_method_id: "m:a",
        executed_method_id: "m:a",
        input_revision: "rev-FOREIGN",
        candidate_sha256: "b".repeat(64),
        elapsed_seconds: 10,
        limit_seconds: 60,
        search_iterations: 1,
        stop_reason: "completed",
        evidence_refs: ["ev:f9:a"],
      },
    }),
  ];

  const report = evaluateScopedYardstickFidelity({
    node_id: "node-opt",
    authoritative: authMax,
    proxy: proxyMin,
    frontier_k: 3,
    findings,
  });

  assert.equal(report.fidelity_status, "frontier_bias_cliff");
  assert.equal(report.calibration_tier, "multi_point");
  assert.equal(report.counts.paired_candidates, 6);
  assert.equal(report.counts.authoritative_unknowns, 1);
  assert.equal(report.counts.proxy_only_candidates, 2);
  assert.ok((report.rank_concordance?.kendall_tau_b ?? 0) > 0.3, "Global concordance remains positive");
  assert.equal(report.frontier_neighborhood?.frontier_kendall_tau_b, -1);
  assert.equal(report.frontier_neighborhood?.bias_cliff_detected, true);
  assert.equal(report.frontier_neighborhood?.authoritative_best_candidate_ref, "cand:c6");
  assert.equal(report.frontier_neighborhood?.proxy_best_paired_candidate_ref, "cand:c4");
  assert.deepEqual(
    report.audit_candidates.unconfirmed_proxy_winners.map(w => w.candidate_ref),
    ["cand:unconfirmed_star"],
  );
  assert.equal(report.advisory_only, true);
  assert.equal(report.grants_execution_authority, false);
});

test("A proxy tie leaves the best candidate unresolved despite positive global concordance", () => {
  const result = evaluateScopedYardstickFidelity({
    node_id: "node-opt", authoritative: authMax, proxy: proxyMin,
    findings: [
      makeFinding("f1", 1, "cand:low", 10, 2),
      makeFinding("f2", 2, "cand:a", 50, 1),
      makeFinding("f3", 3, "cand:b", 60, 1),
    ],
  });
  assert.equal(result.fidelity_status, "aligned_in_tested_scope");
  assert.equal(result.frontier_neighborhood?.authoritative_best_candidate_ref, "cand:b");
  assert.equal(result.frontier_neighborhood?.proxy_best_paired_candidate_ref, null);
  assert.equal(result.frontier_neighborhood?.best_candidate_agrees, null);
});

test("BudgetedContinuationPlan: back-schedules confirmation deadlines, preserves null bounds, and computes exact p*", () => {
  const plan = evaluateBudgetedContinuationPlan({
    remaining_budget: {
      wall_minutes: 180,
      token_budget: null,
      authoritative_eval_slots: 2,
    },
    confirmation: {
      eval_duration_minutes: 45,
      cooldown_minutes: 15,
      eval_tokens: null,
      pending_in_flight_minutes: 10,
    },
    incumbent: {
      candidate_ref: "cand:incumbent",
      value: 0.72,
      higher_is_better: true,
      metric: "score",
      unit: "ratio",
    },
    baseline_route: {
      route_id: "route:continue_tuning",
      summary: "Continue local tuning on current architecture",
      probe_minutes: 0,
      implementation_minutes: 30,
      expected_tokens: 50000,
      requires_authoritative_eval: true,
      expected_gain_min: 0.01,
      expected_gain_expected: 0.02,
      expected_gain_max: 0.04,
    },
    candidate_routes: [
      {
        route_id: "route:reversible_probe_switch",
        summary: "20m diagnostic probe then switch solver",
        kind: "switch_mechanism",
        probe_minutes: 20,
        implementation_minutes: 40,
        expected_tokens: null,
        requires_authoritative_eval: true,
        expected_gain_min: -0.02,
        expected_gain_expected: 0.06,
        expected_gain_max: 0.10,
      },
      {
        route_id: "route:too_late_rewrite",
        summary: "Full rewrite missing confirmation deadline",
        kind: "switch_mechanism",
        probe_minutes: 30,
        implementation_minutes: 96,
        expected_tokens: 120000,
        requires_authoritative_eval: true,
        expected_gain_min: 0.0,
        expected_gain_expected: 0.12,
        expected_gain_max: 0.20,
      },
    ],
  });

  // The 15-minute cooldown starts now and overlaps work after the pending result.
  assert.equal(plan.confirmation.latest_confirmation_start_minute, 135);
  assert.equal(plan.confirmation.remaining_work_window_minutes, 125);
  assert.equal(plan.effective_safe_gain, 0.02);

  const probeRoute = plan.candidate_routes.find(r => r.route_id === "route:reversible_probe_switch")!;
  assert.equal(probeRoute.feasibility_status, "feasible_with_unknown_bounds");
  assert.equal(probeRoute.total_expected_tokens, null, "Unknown token cost stays null, never 0");
  assert.equal(probeRoute.total_wall_minutes, 115);
  assert.equal(probeRoute.reversible_probe_window_feasible, true);
  assert.equal(probeRoute.recommended_disposition, "probe_then_decide");
  // Retained-incumbent floor max(0, -0.02) = 0:
  // p*_optimistic = 0.02 / 0.10 = 0.2; p*_expected = 0.02 / 0.06 = 0.333333
  assert.equal(probeRoute.p_star_optimistic, 0.2);
  assert.equal(probeRoute.p_star_at_expected_gain, 0.333333);

  const lateRoute = plan.candidate_routes.find(r => r.route_id === "route:too_late_rewrite")!;
  assert.equal(lateRoute.feasibility_status, "deadline_infeasible");
  assert.equal(lateRoute.recommended_disposition, "reject_infeasible");
  assert.ok(lateRoute.slack_before_deadline_minutes < 0);
});

test("CounterfactualContinuations: enforces checkpoint & workspace non-interference and computes direction-normalized tau_B", () => {
  const sha = "c".repeat(64);
  const makePair = (id: string, task: string, treatLoss: number, ctrlLoss: number, overrides: Record<string, unknown> = {}) => ({
    pair_id: id,
    task_id: task,
    checkpoint: {
      checkpoint_id: `ckpt:${id}`,
      source_state_sha256: sha,
      input_revision: "rev-1",
      evaluation_ref: "eval:v1",
      metric: "rmse",
      unit: "error",
      higher_is_better: false,
      comparison_scope: "scope:rmse-v1",
      terminal_boundary_ref: "horizon:one-hour",
      incumbent_at_fork: 1.50,
      fork_recorded_at: "2026-07-01T12:00:00Z",
    },
    treatment: {
      arm_id: "arm:budgeted_yardstick",
      checkpoint_id: `ckpt:${id}`,
      source_state_sha256: sha,
      input_revision: "rev-1",
      evaluation_ref: "eval:v1",
      workspace_isolation_id: `ws:${id}:treat`,
      terminal_boundary_ref: "horizon:one-hour",
      decision_context_latest_at: "2026-07-01T12:00:00Z",
      first_action_signature: "act:switch_solver",
      terminal_value: treatLoss,
      valid_submission: true,
      costs: {wall_seconds: 3600, total_tokens: 120000, evaluator_runs: 2, control_plane_requests: 6, recovery_events: null},
      ...(overrides.treatment as object ?? {}),
    },
    control: {
      arm_id: "arm:baseline",
      checkpoint_id: `ckpt:${id}`,
      source_state_sha256: sha,
      input_revision: "rev-1",
      evaluation_ref: "eval:v1",
      workspace_isolation_id: `ws:${id}:ctrl`,
      terminal_boundary_ref: "horizon:one-hour",
      decision_context_latest_at: "2026-07-01T11:59:59Z",
      first_action_signature: "act:tune_hyperparams",
      terminal_value: ctrlLoss,
      valid_submission: true,
      costs: {wall_seconds: 3500, total_tokens: 110000, evaluator_runs: 3, control_plane_requests: 5, recovery_events: 0},
      ...(overrides.control as object ?? {}),
    },
  });

  // Lower rmse is better -> treatment 0.90 vs control 1.20 => tau_B = +0.30
  const pairs = [
    makePair("p1", "task:a", 0.90, 1.20),
    makePair("p2", "task:a", 1.00, 1.25),
    makePair("p3", "task:b", 0.80, 1.10),
    makePair("p4", "task:b", 0.95, 1.15),
  ];

  const cleanEval = evaluateCounterfactualContinuations({pairs});
  assert.equal(cleanEval.all_pairs_identification_valid, true);
  assert.equal(cleanEval.counts.wins, 4);
  assert.equal(cleanEval.counts.action_diverged_pairs, 4);
  // tau_B values: 0.30, 0.25, 0.30, 0.20 -> mean = 0.2625, exact sign-flip p = 2 / 16 = 0.125
  assert.equal(cleanEval.estimand_summary.mean_tau_b, 0.2625);
  assert.equal(cleanEval.estimand_summary.exact_sign_flip_p_value, 0.125);
  assert.equal(cleanEval.complete_marginal_costs.missing_pair_counts.recovery_events, 4);
  const accuracy = makePair("p5", "task:a", 81, 80, {treatment: {first_action_signature: null}});
  Object.assign(accuracy.checkpoint, {metric: "accuracy", unit: "percentage-points", higher_is_better: true});
  const mixed = evaluateCounterfactualContinuations({pairs: [pairs[0], accuracy]});
  assert.equal(mixed.estimand_summary.comparable_scope_count, 2);
  assert.equal(mixed.estimand_summary.mean_tau_b, null);
  assert.equal(mixed.estimand_summary.exact_sign_flip_p_value, null);
  assert.deepEqual(mixed.by_task.map(row => row.mean_tau_b).sort(), [0.3, 1]);
  assert.equal(mixed.pairs[1]?.outcome_classification, "action_unobserved");
  assert.equal(mixed.pairs[1]?.tau_b_direction_normalized, 1);
  const missingClock = makePair("p6", "task:a", 0.9, 1.2, {control: {decision_context_latest_at: null}});
  assert.throws(() => evaluateCounterfactualContinuations({pairs: [missingClock]}), /missing_context_timing/);
  assert.equal(cleanEval.complete_marginal_costs.observed_delta_sums.evaluator_runs, -4);

  // Shared workspace_isolation_id + post-fork future context leakage:
  const contaminated = makePair("p-dirty", "task:c", 0.50, 1.20, {
    treatment: {
      workspace_isolation_id: "ws:p-dirty:ctrl",
      decision_context_latest_at: "2026-07-01T12:05:00Z",
    },
  });
  assert.throws(
    () => evaluateCounterfactualContinuations({strict_identification: true, pairs: [contaminated]}),
    /non-interference|future/,
  );

  const audited = evaluateCounterfactualContinuations({strict_identification: false, pairs: [...pairs, contaminated]});
  assert.equal(audited.all_pairs_identification_valid, false);
  assert.equal(audited.counts.contaminated_pairs, 1);
  assert.equal(audited.counts.valid_pairs, 4);
  assert.equal(audited.estimand_summary.mean_tau_b, 0.2625, "Contaminated pair excluded from estimand");
});

test("evolutionary_population preserves distinct mechanism islands, generates orthogonal crossovers, and quarantines Goodharted proxy winners", async () => {
  const {evaluateEvolutionaryPopulation} = await import(
    "../../loopx/control_plane/capabilities/evolutionary_search.ts"
  );
  const authoritative = {
    metric: "accuracy",
    unit: "pct",
    higher_is_better: true,
    comparison_scope: "bench:test-v1",
    input_revision: "rev-1",
    evaluation_ref: "eval:full",
  };
  const proxy = {
    metric: "proxy_score",
    unit: "pct",
    higher_is_better: true,
    comparison_scope: "bench:dev-v1",
    input_revision: "rev-1",
    evaluation_ref: "eval:fast",
  };

  // Suppose 4 hyperparameter tweaks of mech:alpha score 85.0, 84.8, 84.6, 84.4,
  // while orthogonal mech:beta scores 83.5 and mech:gamma scores 82.0.
  // Plus a Goodharted proxy frontier reversal on top-3 and an unconfirmed proxy star (99.5)!
  const rows: Array<[string, string, number | null, number]> = [
    ["cand:a1", "mech:alpha", 85.0, 86.0],
    ["cand:a2", "mech:alpha", 84.8, 87.0],
    ["cand:a3", "mech:alpha", 84.6, 88.0],
    ["cand:a4", "mech:alpha", 84.4, 70.0],
    ["cand:b1", "mech:beta", 83.5, 68.0],
    ["cand:g1", "mech:gamma", 82.0, 65.0],
    ["cand:proxy_only_star", "mech:alpha", null, 99.5],
  ];

  const findings = rows.map(([cand, , authVal, proxyVal], idx) => ({
    finding_id: `f-${idx}`,
    node_id: "node:evo",
    status: "confirmed",
    recorded_order: idx,
    measurements: [
      {...authoritative, value: authVal, candidate_ref: cand, evidence_ref: `ev:auth:${idx}`},
      {...proxy, value: proxyVal, candidate_ref: cand, evidence_ref: `ev:proxy:${idx}`},
    ],
  }));

  const plan = evaluateEvolutionaryPopulation({
    node_id: "node:evo",
    authoritative,
    proxy,
    findings,
    elite_limit: 3,
    crossover_limit: 2,
    mutation_limit: 2,
    mechanism_by_candidate: Object.fromEntries(rows.map(([cand, mech]) => [cand, mech])),
  });

  assert.equal(plan.selection_status, "multi_island_evolution");
  assert.equal(plan.proxy_fidelity_status, "frontier_bias_cliff");
  assert.equal(plan.counts.confirmed_candidates, 6);
  assert.equal(plan.counts.mechanism_islands, 3);
  assert.equal(plan.counts.authoritative_unknowns, 1);
  // Island elitism: with elite_limit=3 across 3 mechanism islands, champions of mech:alpha, mech:beta,
  // and mech:gamma MUST all survive instead of letting cand:a2/cand:a3 crowd out beta & gamma!
  assert.deepEqual(
    plan.elites.map(e => [e.candidate_ref, e.mechanism_id, e.selection_reason]),
    [
      ["cand:a1", "mech:alpha", "island_champion"],
      ["cand:b1", "mech:beta", "island_champion"],
      ["cand:g1", "mech:gamma", "island_champion"],
    ],
  );
  // Orthogonal crossover pairs combine distinct mechanisms only:
  assert.equal(plan.crossover_pairs.length, 2);
  assert.equal(plan.crossover_pairs[0]?.pair_id, "cand:a1+cand:b1");
  assert.equal(plan.crossover_pairs[1]?.pair_id, "cand:a1+cand:g1");
  // Unconfirmed proxy winner is quarantined due to frontier_bias_cliff, never mixed into elites:
  assert.equal(plan.proxy_quarantine.length, 1);
  assert.equal(plan.proxy_quarantine[0]?.candidate_ref, "cand:proxy_only_star");
  assert.equal(plan.proxy_quarantine[0]?.disposition, "quarantined_proxy_cliff");
  assert.equal(plan.advisory_only, true);
  assert.equal(plan.grants_execution_authority, false);
});
