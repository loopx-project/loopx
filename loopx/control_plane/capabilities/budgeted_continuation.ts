/** Budgeted continuation & confirmation-deadline back-scheduling owner.
 * Advisory research planning only: computes confirmation-deadline feasibility,
 * reversible probe slack, and break-even thresholds without granting authority
 * or coercing unknown resource bounds to zero. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {type JsonObject} from "../runtime_decode.ts";
import {
  assertMethodologyOperatorOutput,
  optionalFiniteNumber,
  requireBoundedSingleLine as singleLine,
  requireExactFields as exactObject,
  requireOpaqueMethodologyRef as opaqueId,
  roundMethodologyMetric as round6,
} from "./stage_methodology_operator.ts";

export const BUDGETED_CONTINUATION_PLAN_SCHEMA = "budgeted_continuation_plan_v0";
export const CONTINUATION_ROUTE_KINDS = [
  "continue_current",
  "switch_mechanism",
  "compatible_combination",
  "bounded_probe",
] as const;
export type ContinuationRouteKind = (typeof CONTINUATION_ROUTE_KINDS)[number];

export const ROUTE_FEASIBILITY_STATUSES = [
  "feasible",
  "feasible_with_unknown_bounds",
  "deadline_infeasible",
  "token_infeasible",
  "eval_slot_infeasible",
] as const;
export type RouteFeasibilityStatus = (typeof ROUTE_FEASIBILITY_STATUSES)[number];

export const ROUTE_DISPOSITIONS = [
  "reject_infeasible",
  "dominated_by_baseline",
  "probe_then_decide",
  "viable_direct_continuation",
  "upside_tail_only",
] as const;
export type RouteDisposition = (typeof ROUTE_DISPOSITIONS)[number];

export interface EvaluatedContinuationRoute {
  route_id: string;
  summary: string;
  kind: ContinuationRouteKind;
  probe_minutes: number;
  implementation_minutes: number;
  confirmation_minutes: number;
  total_wall_minutes: number;
  slack_before_deadline_minutes: number;
  total_expected_tokens: number | null;
  requires_authoritative_eval: boolean;
  feasibility_status: RouteFeasibilityStatus;
  wall_feasible: boolean;
  token_feasible: boolean | null;
  eval_slot_feasible: boolean | null;
  unknown_cost_dimensions: string[];
  reversible_probe_window_feasible: boolean;
  expected_gain_min: number;
  expected_gain_expected: number;
  expected_gain_max: number;
  dominated_by_baseline: boolean;
  p_star_at_expected_gain: number | null;
  p_star_optimistic: number | null;
  recommended_disposition: RouteDisposition;
}

export interface BudgetedContinuationPlan {
  schema_version: typeof BUDGETED_CONTINUATION_PLAN_SCHEMA;
  remaining_budget: {
    wall_minutes: number;
    token_budget: number | null;
    authoritative_eval_slots: number | null;
  };
  confirmation: {
    eval_duration_minutes: number;
    cooldown_minutes: number;
    eval_tokens: number | null;
    pending_in_flight_minutes: number;
    latest_confirmation_start_minute: number;
    remaining_work_window_minutes: number;
  };
  incumbent: {
    candidate_ref: string | null;
    value: number | null;
    higher_is_better: boolean;
    metric: string;
    unit: string;
  };
  baseline_route: EvaluatedContinuationRoute;
  effective_safe_gain: number;
  candidate_routes: EvaluatedContinuationRoute[];
  counts: {
    total_candidates: number;
    feasible_candidates: number;
    deadline_infeasible_candidates: number;
    reversible_probe_candidates: number;
  };
  advisory_only: true;
  grants_execution_authority: false;
}

function nonNegativeNumber(value: unknown, label: string, positive = false): number {
  if (typeof value !== "number" || !Number.isFinite(value) || (positive ? value <= 0 : value < 0)) {
    throw new EffectRuntimeRequestError(`${label} requires a finite ${positive ? "positive" : "non-negative"} number`);
  }
  return value;
}

const nullableNonNegativeNumber = (value: unknown, label: string): number | null =>
  optionalFiniteNumber(value, label, {nonNegative: true});

function nullableNonNegativeInt(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new EffectRuntimeRequestError(`${label} requires a non-negative integer or null`);
  }
  return value;
}

function finiteGain(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new EffectRuntimeRequestError(`${label} requires a finite number`);
  }
  return value;
}

interface NormalizedRouteInput {
  route_id: string;
  summary: string;
  kind: ContinuationRouteKind;
  probe_minutes: number;
  implementation_minutes: number;
  expected_tokens: number | null;
  requires_authoritative_eval: boolean;
  expected_gain_min: number;
  expected_gain_expected: number;
  expected_gain_max: number;
}

function normalizeRoute(value: unknown, label: string, defaultKind?: ContinuationRouteKind): NormalizedRouteInput {
  const allowed = [
    "route_id", "summary", "kind", "probe_minutes", "implementation_minutes",
    "expected_tokens", "requires_authoritative_eval",
    "expected_gain_min", "expected_gain_expected", "expected_gain_max",
  ];
  const required = [
    "route_id", "summary", "probe_minutes", "implementation_minutes",
    "requires_authoritative_eval", "expected_gain_min", "expected_gain_expected", "expected_gain_max",
  ];
  const row = exactObject(value, allowed, defaultKind ? required : [...required, "kind"], label);
  const kindRaw = row.kind === undefined ? defaultKind : row.kind;
  if (typeof kindRaw !== "string" || !(CONTINUATION_ROUTE_KINDS as readonly string[]).includes(kindRaw)) {
    throw new EffectRuntimeRequestError(`${label} kind is unsupported`);
  }
  if (typeof row.requires_authoritative_eval !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} requires explicit requires_authoritative_eval boolean`);
  }
  const gMin = finiteGain(row.expected_gain_min, `${label} expected_gain_min`);
  const gExp = finiteGain(row.expected_gain_expected, `${label} expected_gain_expected`);
  const gMax = finiteGain(row.expected_gain_max, `${label} expected_gain_max`);
  if (gMin > gExp || gExp > gMax) {
    throw new EffectRuntimeRequestError(`${label} requires expected_gain_min <= expected_gain_expected <= expected_gain_max`);
  }
  return {
    route_id: opaqueId(row.route_id, `${label} route_id`),
    summary: singleLine(row.summary, `${label} summary`, 240),
    kind: kindRaw as ContinuationRouteKind,
    probe_minutes: nonNegativeNumber(row.probe_minutes, `${label} probe_minutes`),
    implementation_minutes: nonNegativeNumber(row.implementation_minutes, `${label} implementation_minutes`),
    expected_tokens: nullableNonNegativeNumber(row.expected_tokens, `${label} expected_tokens`),
    requires_authoritative_eval: row.requires_authoritative_eval,
    expected_gain_min: gMin,
    expected_gain_expected: gExp,
    expected_gain_max: gMax,
  };
}

export function evaluateBudgetedContinuationPlan(params: JsonObject): BudgetedContinuationPlan {
  const root = exactObject(
    params,
    ["schema_version", "remaining_budget", "confirmation", "incumbent", "baseline_route", "candidate_routes"],
    ["remaining_budget", "confirmation", "incumbent", "baseline_route", "candidate_routes"],
    "budgeted continuation request",
  );
  if (root.schema_version !== undefined && root.schema_version !== BUDGETED_CONTINUATION_PLAN_SCHEMA) {
    throw new EffectRuntimeRequestError("unsupported budgeted continuation schema");
  }

  const remRaw = exactObject(
    root.remaining_budget,
    ["wall_minutes", "token_budget", "authoritative_eval_slots"],
    ["wall_minutes"],
    "remaining_budget",
  );
  const wallMinutes = nonNegativeNumber(remRaw.wall_minutes, "remaining_budget.wall_minutes", true);
  const tokenBudget = nullableNonNegativeNumber(remRaw.token_budget, "remaining_budget.token_budget");
  const evalSlots = nullableNonNegativeInt(remRaw.authoritative_eval_slots, "remaining_budget.authoritative_eval_slots");

  const confRaw = exactObject(
    root.confirmation,
    ["eval_duration_minutes", "cooldown_minutes", "eval_tokens", "pending_in_flight_minutes"],
    ["eval_duration_minutes"],
    "confirmation",
  );
  const evalDuration = nonNegativeNumber(confRaw.eval_duration_minutes, "confirmation.eval_duration_minutes");
  const cooldown = confRaw.cooldown_minutes === undefined ? 0 : nonNegativeNumber(confRaw.cooldown_minutes, "confirmation.cooldown_minutes");
  const evalTokens = nullableNonNegativeNumber(confRaw.eval_tokens, "confirmation.eval_tokens");
  const inFlight = confRaw.pending_in_flight_minutes === undefined ? 0 : nonNegativeNumber(confRaw.pending_in_flight_minutes, "confirmation.pending_in_flight_minutes");

  const incRaw = exactObject(
    root.incumbent,
    ["candidate_ref", "value", "higher_is_better", "metric", "unit"],
    ["candidate_ref", "value", "higher_is_better", "metric", "unit"],
    "incumbent",
  );
  if (typeof incRaw.higher_is_better !== "boolean") {
    throw new EffectRuntimeRequestError("incumbent requires explicit higher_is_better boolean");
  }
  if (incRaw.value !== null && (typeof incRaw.value !== "number" || !Number.isFinite(incRaw.value))) {
    throw new EffectRuntimeRequestError("incumbent value must be a finite number or null");
  }
  const incumbent = {
    candidate_ref: incRaw.candidate_ref === null ? null : opaqueId(incRaw.candidate_ref, "incumbent.candidate_ref"),
    value: incRaw.value as number | null,
    higher_is_better: incRaw.higher_is_better,
    metric: singleLine(incRaw.metric, "incumbent.metric", 80),
    unit: singleLine(incRaw.unit, "incumbent.unit", 40),
  };

  const baselineInput = normalizeRoute(root.baseline_route, "baseline_route", "continue_current");
  if (!Array.isArray(root.candidate_routes) || root.candidate_routes.length < 1 || root.candidate_routes.length > 16) {
    throw new EffectRuntimeRequestError("candidate_routes requires 1 to 16 routes");
  }
  const candidateInputs = root.candidate_routes.map((r, i) => normalizeRoute(r, `candidate_routes[${i}]`));
  const seenIds = new Set([baselineInput.route_id]);
  for (const c of candidateInputs) {
    if (seenIds.has(c.route_id)) {
      throw new EffectRuntimeRequestError(`duplicate route_id: ${c.route_id}`);
    }
    seenIds.add(c.route_id);
  }

  const latestConfirmStart = round6(wallMinutes - evalDuration);
  const remainingWorkWindow = round6(wallMinutes - inFlight - cooldown - evalDuration);

  const baselineConfirmMinutes = baselineInput.requires_authoritative_eval ? cooldown + evalDuration : 0;
  const baselinePostProbeWall = baselineInput.implementation_minutes + baselineConfirmMinutes;

  const evaluateOne = (route: NormalizedRouteInput, safeGain: number | null): EvaluatedContinuationRoute => {
    const confirmMinutes = route.requires_authoritative_eval ? cooldown + evalDuration : 0;
    const totalWall = round6(inFlight + route.probe_minutes + route.implementation_minutes + confirmMinutes);
    const slack = round6(wallMinutes - totalWall);
    const wallFeasible = slack >= 0;

    const unknownDims: string[] = [];
    let totalTokens: number | null = null;
    if (route.expected_tokens === null || (route.requires_authoritative_eval && evalTokens === null)) {
      unknownDims.push("tokens");
    } else {
      totalTokens = round6(route.expected_tokens + (route.requires_authoritative_eval ? (evalTokens ?? 0) : 0));
    }
    let tokenFeasible: boolean | null = null;
    if (tokenBudget === null) {
      if (!unknownDims.includes("tokens")) unknownDims.push("token_budget");
    } else if (totalTokens !== null) {
      tokenFeasible = totalTokens <= tokenBudget;
    }

    let slotFeasible: boolean | null = null;
    if (!route.requires_authoritative_eval) {
      slotFeasible = true;
    } else if (evalSlots === null) {
      unknownDims.push("authoritative_eval_slots");
    } else {
      slotFeasible = evalSlots >= 1;
    }

    let feasibilityStatus: RouteFeasibilityStatus;
    if (!wallFeasible) feasibilityStatus = "deadline_infeasible";
    else if (slotFeasible === false) feasibilityStatus = "eval_slot_infeasible";
    else if (tokenFeasible === false) feasibilityStatus = "token_infeasible";
    else if (unknownDims.length > 0) feasibilityStatus = "feasible_with_unknown_bounds";
    else feasibilityStatus = "feasible";

    const feasible = feasibilityStatus === "feasible" || feasibilityStatus === "feasible_with_unknown_bounds";
    const reversibleProbe = route.probe_minutes > 0 && feasible
      && round6(inFlight + route.probe_minutes + Math.max(route.implementation_minutes + confirmMinutes, baselinePostProbeWall)) <= wallMinutes;

    const refGain = safeGain ?? 0;
    const dominated = feasible && safeGain !== null && route.expected_gain_max <= refGain;
    let pStarExp: number | null = null;
    let pStarOpt: number | null = null;
    if (feasible && safeGain !== null && !dominated) {
      // Retained-incumbent semantics: a regressed candidate does not overwrite the
      // incumbent, so post-confirmation floor over incumbent is max(0, expected_gain_min).
      const retainedFloor = Math.max(0, route.expected_gain_min);
      if (refGain <= retainedFloor) {
        pStarExp = route.expected_gain_expected > retainedFloor || refGain <= 0 ? 0 : null;
        pStarOpt = route.expected_gain_max > retainedFloor || refGain <= 0 ? 0 : null;
      } else {
        pStarOpt = route.expected_gain_max > retainedFloor
          ? round6(Math.min(1, (refGain - retainedFloor) / (route.expected_gain_max - retainedFloor)))
          : null;
        pStarExp = route.expected_gain_expected >= refGain && route.expected_gain_expected > retainedFloor
          ? round6((refGain - retainedFloor) / (route.expected_gain_expected - retainedFloor))
          : null;
      }
    }

    let disposition: RouteDisposition;
    if (!feasible) disposition = "reject_infeasible";
    else if (safeGain === null) disposition = "viable_direct_continuation";
    else if (dominated) disposition = "dominated_by_baseline";
    else if (reversibleProbe) disposition = "probe_then_decide";
    else if (pStarExp !== null && pStarExp <= 1) disposition = "viable_direct_continuation";
    else disposition = "upside_tail_only";

    return {
      route_id: route.route_id,
      summary: route.summary,
      kind: route.kind,
      probe_minutes: round6(route.probe_minutes),
      implementation_minutes: round6(route.implementation_minutes),
      confirmation_minutes: round6(confirmMinutes),
      total_wall_minutes: totalWall,
      slack_before_deadline_minutes: slack,
      total_expected_tokens: totalTokens,
      requires_authoritative_eval: route.requires_authoritative_eval,
      feasibility_status: feasibilityStatus,
      wall_feasible: wallFeasible,
      token_feasible: tokenFeasible,
      eval_slot_feasible: slotFeasible,
      unknown_cost_dimensions: unknownDims,
      reversible_probe_window_feasible: reversibleProbe,
      expected_gain_min: round6(route.expected_gain_min),
      expected_gain_expected: round6(route.expected_gain_expected),
      expected_gain_max: round6(route.expected_gain_max),
      dominated_by_baseline: dominated,
      p_star_at_expected_gain: pStarExp,
      p_star_optimistic: pStarOpt,
      recommended_disposition: disposition,
    };
  };

  const evaluatedBaseline = evaluateOne(baselineInput, null);
  const baselineFeasible = evaluatedBaseline.feasibility_status === "feasible"
    || evaluatedBaseline.feasibility_status === "feasible_with_unknown_bounds";
  const effectiveSafeGain = baselineFeasible ? Math.max(0, evaluatedBaseline.expected_gain_expected) : 0;

  const evaluatedCandidates = candidateInputs.map(c => evaluateOne(c, effectiveSafeGain));

  return assertMethodologyOperatorOutput("budgeted_continuation", {
    schema_version: BUDGETED_CONTINUATION_PLAN_SCHEMA,
    remaining_budget: {
      wall_minutes: round6(wallMinutes),
      token_budget: tokenBudget === null ? null : round6(tokenBudget),
      authoritative_eval_slots: evalSlots,
    },
    confirmation: {
      eval_duration_minutes: round6(evalDuration),
      cooldown_minutes: round6(cooldown),
      eval_tokens: evalTokens === null ? null : round6(evalTokens),
      pending_in_flight_minutes: round6(inFlight),
      latest_confirmation_start_minute: latestConfirmStart,
      remaining_work_window_minutes: remainingWorkWindow,
    },
    incumbent,
    baseline_route: evaluatedBaseline,
    effective_safe_gain: round6(effectiveSafeGain),
    candidate_routes: evaluatedCandidates,
    counts: {
      total_candidates: evaluatedCandidates.length,
      feasible_candidates: evaluatedCandidates.filter(
        c => c.feasibility_status === "feasible" || c.feasibility_status === "feasible_with_unknown_bounds",
      ).length,
      deadline_infeasible_candidates: evaluatedCandidates.filter(
        c => c.feasibility_status === "deadline_infeasible",
      ).length,
      reversible_probe_candidates: evaluatedCandidates.filter(c => c.reversible_probe_window_feasible).length,
    },
    advisory_only: true,
    grants_execution_authority: false,
  } as unknown as JsonObject) as unknown as BudgetedContinuationPlan;
}
