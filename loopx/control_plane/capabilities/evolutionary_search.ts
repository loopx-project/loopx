/** Mechanism-diverse evolutionary population search operator.
 * Built directly on the 5-stage methodology operator kernel and scoped
 * Yardstick fidelity: selects island-diverse confirmed elites, orthogonal
 * mechanism crossover pairs, and targeted bottleneck mutations while
 * quarantining unconfirmed or Goodhart-cliff proxy winners.
 * Pure, deterministic, browser-safe, and advisory-only. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {
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
import {
  evaluateScopedYardstickFidelity,
  type YardstickFidelityStatus,
} from "./yardstick_fidelity.ts";

export const EVOLUTIONARY_POPULATION_PLAN_SCHEMA = "evolutionary_population_plan_v0";

export const EVOLUTIONARY_SELECTION_STATUSES = [
  "empty_population",
  "single_seed",
  "mechanism_unresolved",
  "single_island_refinement",
  "multi_island_evolution",
] as const;
export type EvolutionarySelectionStatus = (typeof EVOLUTIONARY_SELECTION_STATUSES)[number];

export const PROXY_QUARANTINE_DISPOSITIONS = [
  "confirm_before_breeding",
  "quarantined_proxy_cliff",
] as const;
export type ProxyQuarantineDisposition = (typeof PROXY_QUARANTINE_DISPOSITIONS)[number];

export interface EvolutionaryCandidateMember {
  candidate_ref: string;
  mechanism_id: string | null;
  authoritative_value: number;
  utility_score: number;
  finding_id: string;
  evidence_ref: string;
  island_rank: number | null;
  selection_reason: "island_champion" | "utility_fill";
}

export interface EvolutionaryCrossoverPair {
  pair_id: string;
  parent_a_candidate_ref: string;
  parent_a_mechanism_id: string;
  parent_a_value: number;
  parent_b_candidate_ref: string;
  parent_b_mechanism_id: string;
  parent_b_value: number;
  combined_utility_score: number;
  synthesis_goal: "test_mechanism_combination";
}

export interface EvolutionaryMutationTarget {
  candidate_ref: string;
  mechanism_id: string | null;
  authoritative_value: number;
  mutation_kind: "island_champion_refinement" | "tie_breaking_bottleneck_probe";
}

export interface EvolutionaryPopulationPlan {
  schema_version: typeof EVOLUTIONARY_POPULATION_PLAN_SCHEMA;
  node_id: string;
  authoritative: ExploreMetricProfile;
  proxy: ExploreMetricProfile | null;
  selection_status: EvolutionarySelectionStatus;
  proxy_fidelity_status: YardstickFidelityStatus | null;
  counts: {
    confirmed_candidates: number;
    unknown_mechanism_candidates: number;
    mechanism_islands: number;
    selected_elites: number;
    omitted_candidates: number;
    crossover_pairs: number;
    mutation_targets: number;
    quarantined_proxy_candidates: number;
    authoritative_unknowns: number;
    repeat_observations: number;
  };
  islands: Array<{
    mechanism_id: string;
    member_count: number;
    champion_candidate_ref: string;
    champion_value: number;
  }>;
  omitted_islands: number;
  elites: EvolutionaryCandidateMember[];
  crossover_pairs: EvolutionaryCrossoverPair[];
  mutation_targets: EvolutionaryMutationTarget[];
  proxy_quarantine: Array<{
    candidate_ref: string;
    finding_id: string;
    evidence_ref: string;
    proxy_value: number;
    disposition: ProxyQuarantineDisposition;
  }>;
  omitted_proxy_quarantine: number;
  advisory_only: true;
  grants_execution_authority: false;
}

function boundedInt(value: unknown, fallback: number, min: number, max: number, label: string): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new EffectRuntimeRequestError(`${label} requires an integer in ${min}..${max}`);
  }
  return value;
}

function normalizeMechanismOverrides(value: unknown): Map<string, string> {
  if (value === undefined || value === null) return new Map();
  const row = requireJsonObject(value, "mechanism_by_candidate");
  const out = new Map<string, string>();
  for (const [cand, mech] of Object.entries(row)) {
    out.set(requireOpaqueMethodologyRef(cand, "mechanism_by_candidate key"), requireOpaqueMethodologyRef(mech, "mechanism_id"));
  }
  return out;
}

export function evaluateEvolutionaryPopulation(params: JsonObject): EvolutionaryPopulationPlan {
  const nodeId = requireOpaqueMethodologyRef(params.node_id, "node_id");
  const authoritative = normalizeExploreMetricProfile(params.authoritative, "authoritative profile");
  const proxy =
    params.proxy === null || params.proxy === undefined
      ? null
      : normalizeExploreMetricProfile(params.proxy, "proxy profile");
  const eliteLimit = boundedInt(params.elite_limit, 4, 2, 12, "elite_limit");
  const crossoverLimit = boundedInt(params.crossover_limit, 2, 0, 6, "crossover_limit");
  const mutationLimit = boundedInt(params.mutation_limit, 2, 0, 6, "mutation_limit");
  const mechanismOverrides = normalizeMechanismOverrides(params.mechanism_by_candidate);

  if (!Array.isArray(params.findings)) {
    throw new EffectRuntimeRequestError("findings requires an array of Explore finding records");
  }
  const rawFindings = (params.findings as unknown[]).map(row => requireJsonObject(row, "Explore finding"));
  const ordered = orderCanonicalExploreFindings(rawFindings.filter(row => String(row.node_id ?? "") === nodeId));

  type ObservedCandidate = {
    candidate_ref: string;
    mechanism_id: string | null;
    value: number;
    utility: number;
    finding_id: string;
    evidence_ref: string;
  };
  const byCandidate = new Map<string, ObservedCandidate>();
  let authUnknowns = 0;
  let repeats = 0;

  for (const finding of ordered) {
    const findingId = String(finding.finding_id ?? finding.result_id ?? "");
    if (!findingId) continue;
    const {measurements, methodId} = extractVerifiedFindingMeasurements(finding);
    for (const m of measurements) {
      if (!matchesExploreMetricProfile(m, authoritative)) {
        continue;
      }
      if (m.value === null) {
        authUnknowns++;
        continue;
      }
      const previous = byCandidate.get(m.candidate_ref);
      if (previous) repeats++;
      const mechanismId =
        mechanismOverrides.get(m.candidate_ref) ??
        methodId ??
        previous?.mechanism_id ??
        null;
      const utility = round6(authoritative.higher_is_better ? m.value : -m.value);
      byCandidate.set(m.candidate_ref, {
        candidate_ref: m.candidate_ref,
        mechanism_id: mechanismId,
        value: m.value,
        utility,
        finding_id: findingId,
        evidence_ref: m.evidence_ref,
      });
    }
  }

  const fidelity = proxy
    ? evaluateScopedYardstickFidelity({
        node_id: nodeId,
        authoritative: authoritative as unknown as JsonObject,
        proxy: proxy as unknown as JsonObject,
        findings: rawFindings,
      })
    : null;

  // Group confirmed candidates into mechanism islands.
  const islandMembers = new Map<string, ObservedCandidate[]>();
  for (const cand of byCandidate.values()) {
    if (cand.mechanism_id === null) continue;
    const list = islandMembers.get(cand.mechanism_id) ?? [];
    list.push(cand);
    islandMembers.set(cand.mechanism_id, list);
  }
  for (const list of islandMembers.values()) {
    list.sort((a, b) => b.utility - a.utility || a.candidate_ref.localeCompare(b.candidate_ref));
  }
  const sortedIslands = [...islandMembers.entries()].sort(([, left], [, right]) => {
    const diff = right[0]!.utility - left[0]!.utility;
    return diff !== 0 ? diff : left[0]!.candidate_ref.localeCompare(right[0]!.candidate_ref);
  });

  // Pass 1: preserve one champion per distinct mechanism island (anti-premature-convergence).
  const selectedIds = new Set<string>();
  const elites: EvolutionaryCandidateMember[] = [];
  const rankedCandidates = [...byCandidate.values()].sort(
    (a, b) => b.utility - a.utility || a.candidate_ref.localeCompare(b.candidate_ref),
  );
  const ranks = new Map<string, number>();
  for (const [, members] of sortedIslands) {
    members.forEach((member, index) => ranks.set(member.candidate_ref, index + 1));
  }
  const keep = (candidate: ObservedCandidate, reason: EvolutionaryCandidateMember["selection_reason"]) => {
    selectedIds.add(candidate.candidate_ref);
    elites.push({
      candidate_ref: candidate.candidate_ref,
      mechanism_id: candidate.mechanism_id,
      authoritative_value: candidate.value,
      utility_score: candidate.utility,
      finding_id: candidate.finding_id,
      evidence_ref: candidate.evidence_ref,
      island_rank: ranks.get(candidate.candidate_ref) ?? null,
      selection_reason: reason,
    });
  };
  const best = rankedCandidates[0];
  if (best?.mechanism_id === null) keep(best, "utility_fill");
  for (const [, members] of sortedIslands) {
    if (elites.length >= eliteLimit) break;
    keep(members[0]!, "island_champion");
  }

  // Pass 2: fill remaining elite slots by highest confirmed utility across islands.
  for (const candidate of rankedCandidates) {
    if (elites.length >= eliteLimit) break;
    if (!selectedIds.has(candidate.candidate_ref)) keep(candidate, "utility_fill");
  }
  elites.sort((a, b) => b.utility_score - a.utility_score || a.candidate_ref.localeCompare(b.candidate_ref));

  // Distinct mechanisms give a combination to test. Compatibility still needs evidence.
  const rawCrossovers: EvolutionaryCrossoverPair[] = [];
  for (let i = 0; i < elites.length; i++) {
    for (let j = i + 1; j < elites.length; j++) {
      const a = elites[i]!;
      const b = elites[j]!;
      if (a.mechanism_id === null || b.mechanism_id === null || a.mechanism_id === b.mechanism_id) continue;
      rawCrossovers.push({
        pair_id: `${a.candidate_ref}+${b.candidate_ref}`,
        parent_a_candidate_ref: a.candidate_ref,
        parent_a_mechanism_id: a.mechanism_id,
        parent_a_value: a.authoritative_value,
        parent_b_candidate_ref: b.candidate_ref,
        parent_b_mechanism_id: b.mechanism_id,
        parent_b_value: b.authoritative_value,
        combined_utility_score: round6(a.utility_score + b.utility_score),
        synthesis_goal: "test_mechanism_combination",
      });
    }
  }
  rawCrossovers.sort(
    (a, b) => b.combined_utility_score - a.combined_utility_score || a.pair_id.localeCompare(b.pair_id),
  );
  const crossoverPairs =
    crossoverLimit === 0
      ? []
      : projectBoundedMethodologyItems(rawCrossovers, crossoverLimit).visible;

  // Targeted mutations on top elites.
  const knownElites = elites.filter(elite => elite.mechanism_id !== null);
  const topTied = knownElites.length >= 2 && knownElites[0]!.utility_score === knownElites[1]!.utility_score;
  const rawMutations: EvolutionaryMutationTarget[] = knownElites.map((elite, idx) => ({
    candidate_ref: elite.candidate_ref,
    mechanism_id: elite.mechanism_id,
    authoritative_value: elite.authoritative_value,
    mutation_kind:
      topTied && idx < 2 ? "tie_breaking_bottleneck_probe" : "island_champion_refinement",
  }));
  const mutationTargets =
    mutationLimit === 0
      ? []
      : projectBoundedMethodologyItems(rawMutations, mutationLimit).visible;

  // Goodhart-protected proxy quarantine: never promote proxy-only winners into elites directly.
  const proxyCliff =
    fidelity?.fidelity_status === "frontier_bias_cliff" ||
    fidelity?.fidelity_status === "order_reversed";
  const quarantineDisposition: ProxyQuarantineDisposition = proxyCliff
    ? "quarantined_proxy_cliff"
    : "confirm_before_breeding";
  const rawQuarantine = (fidelity?.audit_candidates.unconfirmed_proxy_winners ?? []).map(item => ({
    candidate_ref: item.candidate_ref,
    finding_id: item.finding_id,
    evidence_ref: item.evidence_ref,
    proxy_value: item.proxy_value,
    disposition: quarantineDisposition,
  }));
  const boundedQuarantine = projectBoundedMethodologyItems(rawQuarantine, 8);
  const boundedIslands = projectBoundedMethodologyItems(
    sortedIslands.map(([mechId, members]) => ({
      mechanism_id: mechId,
      member_count: members.length,
      champion_candidate_ref: members[0]!.candidate_ref,
      champion_value: members[0]!.value,
    })),
    12,
  );

  const selectionStatus: EvolutionarySelectionStatus =
    byCandidate.size === 0
      ? "empty_population"
      : byCandidate.size === 1
        ? "single_seed"
        : sortedIslands.length === 0
          ? "mechanism_unresolved"
          : sortedIslands.length === 1
            ? "single_island_refinement"
            : "multi_island_evolution";

  return assertMethodologyOperatorOutput("evolutionary_population", {
    schema_version: EVOLUTIONARY_POPULATION_PLAN_SCHEMA,
    node_id: nodeId,
    authoritative,
    proxy,
    selection_status: selectionStatus,
    proxy_fidelity_status: fidelity?.fidelity_status ?? null,
    counts: {
      confirmed_candidates: byCandidate.size,
      unknown_mechanism_candidates: rankedCandidates.filter(candidate => candidate.mechanism_id === null).length,
      mechanism_islands: sortedIslands.length,
      selected_elites: elites.length,
      omitted_candidates: Math.max(0, byCandidate.size - elites.length),
      crossover_pairs: crossoverPairs.length,
      mutation_targets: mutationTargets.length,
      quarantined_proxy_candidates: rawQuarantine.length,
      authoritative_unknowns: authUnknowns,
      repeat_observations: repeats,
    },
    islands: boundedIslands.visible,
    omitted_islands: boundedIslands.omitted_count,
    elites,
    crossover_pairs: crossoverPairs,
    mutation_targets: mutationTargets,
    proxy_quarantine: boundedQuarantine.visible,
    omitted_proxy_quarantine: boundedQuarantine.omitted_count,
    advisory_only: true,
    grants_execution_authority: false,
  } as unknown as JsonObject) as unknown as EvolutionaryPopulationPlan;
}
