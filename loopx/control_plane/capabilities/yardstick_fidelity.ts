/** Scoped multi-point Yardstick fidelity and frontier bias-cliff analysis.
 * Pure, deterministic, browser-compatible owner; never blacklists an evaluator
 * globally or grants acceptance/execution authority. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {
  type ExploreMeasurement,
  extractVerifiedFindingMeasurements,
  matchesExploreMetricProfile,
  normalizeExploreMetricProfile,
  orderCanonicalExploreFindings,
  type ExploreMetricProfile,
} from "./explore_measurements.ts";
import {
  assertMethodologyOperatorOutput,
  projectBoundedMethodologyItems,
  requireOpaqueMethodologyRef,
  roundMethodologyMetric as round6,
} from "./stage_methodology_operator.ts";

export const SCOPED_YARDSTICK_FIDELITY_SCHEMA = "scoped_yardstick_fidelity_v0";
export const YARDSTICK_FIDELITY_STATUSES = [
  "direct_only",
  "insufficient_paired_evidence",
  "noisy_indistinguishable",
  "aligned_in_tested_scope",
  "frontier_bias_cliff",
  "order_reversed",
  "mixed_or_flat",
] as const;
export type YardstickFidelityStatus = (typeof YARDSTICK_FIDELITY_STATUSES)[number];
export const YARDSTICK_CALIBRATION_TIERS = ["none", "two_point", "multi_point"] as const;
export type YardstickCalibrationTier = (typeof YARDSTICK_CALIBRATION_TIERS)[number];

export type YardstickMetricProfile = ExploreMetricProfile;
export const normalizeYardstickMetricProfile = normalizeExploreMetricProfile;

export interface PairedYardstickCandidate {
  candidate_ref: string;
  authoritative_value: number;
  authoritative_finding_id: string;
  authoritative_evidence_ref: string;
  proxy_value: number;
  proxy_finding_id: string;
  proxy_evidence_ref: string;
}

export interface ScopedYardstickFidelity {
  schema_version: typeof SCOPED_YARDSTICK_FIDELITY_SCHEMA;
  node_id: string;
  authoritative: YardstickMetricProfile;
  proxy: YardstickMetricProfile | null;
  fidelity_status: YardstickFidelityStatus;
  calibration_tier: YardstickCalibrationTier;
  counts: {
    paired_candidates: number;
    authoritative_only_candidates: number;
    proxy_only_candidates: number;
    authoritative_unknowns: number;
    proxy_unknowns: number;
    repeat_observations: number;
  };
  rank_concordance: {
    concordant_pairs: number;
    discordant_pairs: number;
    authoritative_ties: number;
    proxy_ties: number;
    joint_ties: number;
    kendall_tau_b: number | null;
    spearman_rho: number | null;
  } | null;
  linear_calibration: {
    utility_slope: number;
    utility_intercept: number;
    raw_slope: number;
    raw_intercept: number;
    residual_mad: number;
    r_squared: number | null;
  } | null;
  frontier_neighborhood: {
    frontier_k: number;
    concordant_pairs: number;
    discordant_pairs: number;
    frontier_kendall_tau_b: number | null;
    authoritative_best_candidate_ref: string | null;
    proxy_best_paired_candidate_ref: string | null;
    best_candidate_agrees: boolean | null;
    bias_cliff_detected: boolean;
  } | null;
  audit_candidates: {
    unconfirmed_proxy_winners: Array<{
      candidate_ref: string;
      finding_id: string;
      evidence_ref: string;
      proxy_value: number;
    }>;
    proxy_false_negatives: Array<{
      candidate_ref: string;
      authoritative_value: number;
      proxy_value: number;
    }>;
  };
  paired_points: PairedYardstickCandidate[];
  omitted_paired_points: number;
  scope_bound: true;
  global_evaluator_blacklist: false;
  advisory_only: true;
  grants_execution_authority: false;
}

const PAIRED_POINT_CAP = 24;

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

function averageRanks(values: readonly number[]): number[] {
  const indexed = values.map((v, idx) => ({v, idx})).sort((a, b) => a.v - b.v);
  const ranks = new Array<number>(values.length);
  let i = 0;
  while (i < indexed.length) {
    let j = i;
    while (j + 1 < indexed.length && indexed[j + 1]!.v === indexed[i]!.v) j++;
    const avg = (i + j + 2) / 2;
    for (let k = i; k <= j; k++) ranks[indexed[k]!.idx] = avg;
    i = j + 1;
  }
  return ranks;
}

function pearson(xs: readonly number[], ys: readonly number[]): number | null {
  const n = xs.length;
  if (n < 2) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, dx2 = 0, dy2 = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i]! - mx, dy = ys[i]! - my;
    num += dx * dy; dx2 += dx * dx; dy2 += dy * dy;
  }
  const denom = Math.sqrt(dx2 * dy2);
  return denom > 0 ? round6(Math.max(-1, Math.min(1, num / denom))) : null;
}

function pairwiseConcordance(authUtil: readonly number[], proxyUtil: readonly number[]) {
  let concordant = 0, discordant = 0, authTies = 0, proxyTies = 0, jointTies = 0;
  for (let i = 0; i < authUtil.length; i++) {
    for (let j = i + 1; j < authUtil.length; j++) {
      const da = authUtil[i]! - authUtil[j]!;
      const dp = proxyUtil[i]! - proxyUtil[j]!;
      if (da === 0 && dp === 0) jointTies++;
      else if (da === 0) authTies++;
      else if (dp === 0) proxyTies++;
      else if ((da > 0) === (dp > 0)) concordant++;
      else discordant++;
    }
  }
  const denom = Math.sqrt((concordant + discordant + authTies) * (concordant + discordant + proxyTies));
  const tauB = denom > 0 ? round6((concordant - discordant) / denom) : null;
  const rho = pearson(averageRanks(authUtil), averageRanks(proxyUtil));
  return {
    concordant_pairs: concordant,
    discordant_pairs: discordant,
    authoritative_ties: authTies,
    proxy_ties: proxyTies,
    joint_ties: jointTies,
    kendall_tau_b: tauB,
    spearman_rho: rho,
  };
}

function linearFit(xs: readonly number[], ys: readonly number[]) {
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxx = 0, sxy = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i]! - mx, dy = ys[i]! - my;
    sxx += dx * dx; sxy += dx * dy; syy += dy * dy;
  }
  if (sxx <= 0) return null;
  const slope = sxy / sxx;
  const intercept = my - slope * mx;
  const residuals = xs.map((x, i) => Math.abs(ys[i]! - (slope * x + intercept)));
  const sse = xs.reduce((acc, x, i) => {
    const err = ys[i]! - (slope * x + intercept);
    return acc + err * err;
  }, 0);
  const r2 = syy > 0 ? round6(Math.max(0, Math.min(1, 1 - sse / syy))) : null;
  return {slope: round6(slope), intercept: round6(intercept), residualMad: round6(median(residuals)), r2};
}

export function evaluateScopedYardstickFidelity(
  params: JsonObject,
  includeMeasurement?: (finding: JsonObject, measurement: ExploreMeasurement) => boolean,
): ScopedYardstickFidelity {
  const nodeId = requireOpaqueMethodologyRef(params.node_id, "node_id");
  const authoritative = normalizeYardstickMetricProfile(params.authoritative, "authoritative profile");
  const proxy = params.proxy === null || params.proxy === undefined
    ? null
    : normalizeYardstickMetricProfile(params.proxy, "proxy profile");
  if (proxy && JSON.stringify(proxy) === JSON.stringify(authoritative)) {
    throw new EffectRuntimeRequestError("proxy and authoritative profiles must be distinct");
  }
  if (!Array.isArray(params.findings)) {
    throw new EffectRuntimeRequestError("findings requires an array of Explore finding records");
  }
  const rawFindings = (params.findings as unknown[]).map(row => requireJsonObject(row, "Explore finding"));
  const ordered = orderCanonicalExploreFindings(rawFindings.filter(row => String(row.node_id ?? "") === nodeId));

  type Obs = {value: number; finding_id: string; evidence_ref: string};
  const authByCand = new Map<string, Obs>();
  const proxyByCand = new Map<string, Obs>();
  let authUnknowns = 0, proxyUnknowns = 0, repeats = 0;

  for (const finding of ordered) {
    const findingId = String(finding.finding_id ?? finding.result_id ?? "");
    if (!findingId) continue;
    for (const m of extractVerifiedFindingMeasurements(finding).measurements) {
      if (includeMeasurement && !includeMeasurement(finding, m)) continue;
      if (matchesExploreMetricProfile(m, authoritative)) {
        if (m.value === null) authUnknowns++;
        else {
          if (authByCand.has(m.candidate_ref)) repeats++;
          authByCand.set(m.candidate_ref, {value: m.value, finding_id: findingId, evidence_ref: m.evidence_ref});
        }
      } else if (proxy && matchesExploreMetricProfile(m, proxy)) {
        if (m.value === null) proxyUnknowns++;
        else {
          if (proxyByCand.has(m.candidate_ref)) repeats++;
          proxyByCand.set(m.candidate_ref, {value: m.value, finding_id: findingId, evidence_ref: m.evidence_ref});
        }
      }
    }
  }

  if (!proxy) {
    return assertMethodologyOperatorOutput("scoped_yardstick_fidelity", {
      schema_version: SCOPED_YARDSTICK_FIDELITY_SCHEMA,
      node_id: nodeId,
      authoritative,
      proxy: null,
      fidelity_status: "direct_only",
      calibration_tier: "none",
      counts: {
        paired_candidates: 0,
        authoritative_only_candidates: authByCand.size,
        proxy_only_candidates: 0,
        authoritative_unknowns: authUnknowns,
        proxy_unknowns: 0,
        repeat_observations: repeats,
      },
      rank_concordance: null,
      linear_calibration: null,
      frontier_neighborhood: null,
      audit_candidates: {unconfirmed_proxy_winners: [], proxy_false_negatives: []},
      paired_points: [],
      omitted_paired_points: 0,
      scope_bound: true,
      global_evaluator_blacklist: false,
      advisory_only: true,
      grants_execution_authority: false,
    } as unknown as JsonObject) as unknown as ScopedYardstickFidelity;
  }

  const authUtil = (v: number) => authoritative.higher_is_better ? v : -v;
  const proxyUtil = (v: number) => proxy.higher_is_better ? v : -v;

  const pairedAll: PairedYardstickCandidate[] = [];
  let authOnly = 0;
  for (const [cand, aObs] of authByCand.entries()) {
    const pObs = proxyByCand.get(cand);
    if (pObs) {
      pairedAll.push({
        candidate_ref: cand,
        authoritative_value: aObs.value,
        authoritative_finding_id: aObs.finding_id,
        authoritative_evidence_ref: aObs.evidence_ref,
        proxy_value: pObs.value,
        proxy_finding_id: pObs.finding_id,
        proxy_evidence_ref: pObs.evidence_ref,
      });
    } else {
      authOnly++;
    }
  }
  pairedAll.sort((a, b) => authUtil(b.authoritative_value) - authUtil(a.authoritative_value)
    || a.candidate_ref.localeCompare(b.candidate_ref));

  const proxyOnlyRows: Array<{candidate_ref: string; finding_id: string; evidence_ref: string; proxy_value: number}> = [];
  for (const [cand, pObs] of proxyByCand.entries()) {
    if (!authByCand.has(cand)) {
      proxyOnlyRows.push({
        candidate_ref: cand,
        finding_id: pObs.finding_id,
        evidence_ref: pObs.evidence_ref,
        proxy_value: pObs.value,
      });
    }
  }
  proxyOnlyRows.sort((a, b) => proxyUtil(b.proxy_value) - proxyUtil(a.proxy_value)
    || a.candidate_ref.localeCompare(b.candidate_ref));

  const n = pairedAll.length;
  const bestPairedProxyUtil = n > 0
    ? Math.max(...pairedAll.map(p => proxyUtil(p.proxy_value)))
    : Number.NEGATIVE_INFINITY;
  const unconfirmedWinners = n > 0
    ? proxyOnlyRows.filter(r => proxyUtil(r.proxy_value) > bestPairedProxyUtil).slice(0, 3)
    : proxyOnlyRows.slice(0, 3);

  const tier: YardstickCalibrationTier = n >= 3 ? "multi_point" : n === 2 ? "two_point" : "none";
  if (n < 2) {
    return assertMethodologyOperatorOutput("scoped_yardstick_fidelity", {
      schema_version: SCOPED_YARDSTICK_FIDELITY_SCHEMA,
      node_id: nodeId,
      authoritative,
      proxy,
      fidelity_status: "insufficient_paired_evidence",
      calibration_tier: tier,
      counts: {
        paired_candidates: n,
        authoritative_only_candidates: authOnly,
        proxy_only_candidates: proxyOnlyRows.length,
        authoritative_unknowns: authUnknowns,
        proxy_unknowns: proxyUnknowns,
        repeat_observations: repeats,
      },
      rank_concordance: null,
      linear_calibration: null,
      frontier_neighborhood: null,
      audit_candidates: {unconfirmed_proxy_winners: unconfirmedWinners, proxy_false_negatives: []},
      paired_points: pairedAll,
      omitted_paired_points: 0,
      scope_bound: true,
      global_evaluator_blacklist: false,
      advisory_only: true,
      grants_execution_authority: false,
    } as unknown as JsonObject) as unknown as ScopedYardstickFidelity;
  }

  const aUtils = pairedAll.map(p => authUtil(p.authoritative_value));
  const pUtils = pairedAll.map(p => proxyUtil(p.proxy_value));
  const concordance = pairwiseConcordance(aUtils, pUtils);

  let linearCal: ScopedYardstickFidelity["linear_calibration"] = null;
  if (n >= 3) {
    const uFit = linearFit(pUtils, aUtils);
    const rFit = linearFit(pairedAll.map(p => p.proxy_value), pairedAll.map(p => p.authoritative_value));
    if (uFit && rFit) {
      linearCal = {
        utility_slope: uFit.slope,
        utility_intercept: uFit.intercept,
        raw_slope: rFit.slope,
        raw_intercept: rFit.intercept,
        residual_mad: uFit.residualMad,
        r_squared: uFit.r2,
      };
    }
  }

  const requestedK = params.frontier_k === undefined
    ? Math.min(n, Math.max(3, Math.ceil(n / 2)))
    : Number(params.frontier_k);
  if (!Number.isSafeInteger(requestedK) || requestedK < 2) {
    throw new EffectRuntimeRequestError("frontier_k must be an integer >= 2");
  }
  const frontierK = Math.min(n, requestedK);
  const frontierSlice = pairedAll.slice(0, frontierK);
  const frontierConcordance = pairwiseConcordance(
    frontierSlice.map(p => authUtil(p.authoritative_value)),
    frontierSlice.map(p => proxyUtil(p.proxy_value)),
  );
  const authBest = aUtils[0]! > aUtils[1]! ? pairedAll[0]!.candidate_ref : null;
  const byProxyDesc = [...pairedAll].sort((a, b) => proxyUtil(b.proxy_value) - proxyUtil(a.proxy_value)
    || a.candidate_ref.localeCompare(b.candidate_ref));
  const proxyBest = proxyUtil(byProxyDesc[0]!.proxy_value) > proxyUtil(byProxyDesc[1]!.proxy_value)
    ? byProxyDesc[0]!.candidate_ref
    : null;
  const bestAgrees = authBest !== null && proxyBest !== null ? authBest === proxyBest : null;

  const biasCliff = n >= 4
    && concordance.kendall_tau_b !== null && concordance.kendall_tau_b > 0
    && ((frontierConcordance.kendall_tau_b !== null && frontierConcordance.kendall_tau_b < 0)
      || (proxyBest !== null && pairedAll.findIndex(p => p.candidate_ref === proxyBest) >= Math.ceil(n / 2)));

  let status: YardstickFidelityStatus;
  if (concordance.kendall_tau_b === null) {
    status = "noisy_indistinguishable";
  } else if (biasCliff) {
    status = "frontier_bias_cliff";
  } else if (concordance.kendall_tau_b < 0) {
    status = "order_reversed";
  } else if (concordance.kendall_tau_b > 0) {
    status = "aligned_in_tested_scope";
  } else {
    status = "mixed_or_flat";
  }

  const authMedian = median(aUtils);
  const proxyMedian = median(pUtils);
  const falseNegatives = n >= 3
    ? pairedAll
        .filter(p => authUtil(p.authoritative_value) > authMedian && proxyUtil(p.proxy_value) < proxyMedian)
        .slice(0, 3)
        .map(p => ({
          candidate_ref: p.candidate_ref,
          authoritative_value: p.authoritative_value,
          proxy_value: p.proxy_value,
        }))
    : [];

  const boundedPairs = projectBoundedMethodologyItems(pairedAll, PAIRED_POINT_CAP);
  return assertMethodologyOperatorOutput("scoped_yardstick_fidelity", {
    schema_version: SCOPED_YARDSTICK_FIDELITY_SCHEMA,
    node_id: nodeId,
    authoritative,
    proxy,
    fidelity_status: status,
    calibration_tier: tier,
    counts: {
      paired_candidates: n,
      authoritative_only_candidates: authOnly,
      proxy_only_candidates: proxyOnlyRows.length,
      authoritative_unknowns: authUnknowns,
      proxy_unknowns: proxyUnknowns,
      repeat_observations: repeats,
    },
    rank_concordance: concordance,
    linear_calibration: linearCal,
    frontier_neighborhood: {
      frontier_k: frontierK,
      concordant_pairs: frontierConcordance.concordant_pairs,
      discordant_pairs: frontierConcordance.discordant_pairs,
      frontier_kendall_tau_b: frontierConcordance.kendall_tau_b,
      authoritative_best_candidate_ref: authBest,
      proxy_best_paired_candidate_ref: proxyBest,
      best_candidate_agrees: bestAgrees,
      bias_cliff_detected: biasCliff,
    },
    audit_candidates: {
      unconfirmed_proxy_winners: unconfirmedWinners,
      proxy_false_negatives: falseNegatives,
    },
    paired_points: boundedPairs.visible,
    omitted_paired_points: boundedPairs.omitted_count,
    scope_bound: true,
    global_evaluator_blacklist: false,
    advisory_only: true,
    grants_execution_authority: false,
  } as unknown as JsonObject) as unknown as ScopedYardstickFidelity;
}
