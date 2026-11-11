/** Same-checkpoint counterfactual continuation pair evaluator.
 * Enforces checkpoint lineage, workspace non-interference, and future-leakage
 * guards; computes direction-normalized tau_B, action-adoption trace, and
 * complete marginal costs while preserving unknown (null) cost dimensions. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {type JsonObject} from "../runtime_decode.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {
  assertMethodologyOperatorOutput,
  isValidContentSha256,
  optionalFiniteNumber,
  requireBoundedSingleLine as singleLine,
  requireExactFields as exactObject,
  requireOpaqueMethodologyRef as opaqueRef,
  roundMethodologyMetric as round6,
} from "./stage_methodology_operator.ts";

export const COUNTERFACTUAL_CONTINUATION_EVAL_SCHEMA = "counterfactual_continuation_eval_v0";
export const COUNTERFACTUAL_COST_DIMENSIONS = [
  "wall_seconds",
  "total_tokens",
  "evaluator_runs",
  "control_plane_requests",
  "recovery_events",
] as const;
export type CounterfactualCostDimension = (typeof COUNTERFACTUAL_COST_DIMENSIONS)[number];

export const COUNTERFACTUAL_PAIR_OUTCOMES = [
  "diverged_with_gain",
  "diverged_no_gain",
  "diverged_regressed",
  "action_identical",
  "action_unobserved",
  "unscored_or_incomplete",
  "contaminated_excluded",
] as const;
export type CounterfactualPairOutcome = (typeof COUNTERFACTUAL_PAIR_OUTCOMES)[number];

export type CounterfactualCostVector = Record<CounterfactualCostDimension, number | null>;

type ComparisonScope = {
  metric: string;
  unit: string;
  higher_is_better: boolean;
  comparison_scope: string;
  input_revision: string;
  evaluation_ref: string;
  terminal_boundary_ref: string;
};

export interface EvaluatedCounterfactualPair {
  pair_id: string;
  task_id: string;
  checkpoint_id: string;
  scope: ComparisonScope;
  identification_valid: boolean;
  identification_violations: string[];
  outcome_classification: CounterfactualPairOutcome;
  action_diverged: boolean | null;
  treatment_arm_id: string;
  control_arm_id: string;
  treatment_terminal_value: number | null;
  control_terminal_value: number | null;
  raw_terminal_delta: number | null;
  tau_b_direction_normalized: number | null;
  treatment_gain_over_incumbent: number | null;
  control_gain_over_incumbent: number | null;
  marginal_cost_delta: CounterfactualCostVector;
  unknown_cost_dimensions: CounterfactualCostDimension[];
}

export interface CounterfactualContinuationEvaluation {
  schema_version: typeof COUNTERFACTUAL_CONTINUATION_EVAL_SCHEMA;
  strict_identification: boolean;
  all_pairs_identification_valid: boolean;
  counts: {
    total_pairs: number;
    valid_pairs: number;
    contaminated_pairs: number;
    scored_valid_pairs: number;
    unscored_valid_pairs: number;
    action_diverged_pairs: number;
    action_identical_pairs: number;
    action_unobserved_pairs: number;
    wins: number;
    ties: number;
    losses: number;
  };
  estimand_summary: {
    comparable_scope_count: number;
    mean_tau_b: number | null;
    median_tau_b: number | null;
    min_tau_b: number | null;
    max_tau_b: number | null;
    exact_sign_flip_p_value: number | null;
    binomial_sign_p_value: number | null;
  };
  complete_marginal_costs: {
    observed_delta_sums: Record<CounterfactualCostDimension, number>;
    observed_pair_counts: Record<CounterfactualCostDimension, number>;
    missing_pair_counts: Record<CounterfactualCostDimension, number>;
  };
  by_task: Array<{
    task_id: string;
    scope: ComparisonScope;
    valid_pairs: number;
    scored_pairs: number;
    mean_tau_b: number | null;
    wins: number;
    ties: number;
    losses: number;
  }>;
  pairs: EvaluatedCounterfactualPair[];
  advisory_only: true;
  grants_execution_authority: false;
}

function sha256Hex(value: unknown, label: string): string {
  const text = singleLine(value, label, 64);
  if (!isValidContentSha256(text)) {
    throw new EffectRuntimeRequestError(`${label} requires a 64-character lowercase hex SHA-256 digest`);
  }
  return text;
}

const nullableFinite = (value: unknown, label: string, nonNegative = false): number | null =>
  optionalFiniteNumber(value, label, {nonNegative});

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

function parseOptionalTimestamp(value: unknown, label: string): bigint | null {
  if (value === null || value === undefined) return null;
  const text = singleLine(value, label, 64);
  const parsed = parseTodoTimestampMicros(text);
  if (parsed === null) {
    throw new EffectRuntimeRequestError(`${label} requires an explicit ISO-8601 UTC/offset timestamp`);
  }
  return parsed;
}

function normalizeCostVector(value: unknown, label: string): CounterfactualCostVector {
  const row = exactObject(value, COUNTERFACTUAL_COST_DIMENSIONS, [], label);
  return {
    wall_seconds: nullableFinite(row.wall_seconds, `${label}.wall_seconds`, true),
    total_tokens: nullableFinite(row.total_tokens, `${label}.total_tokens`, true),
    evaluator_runs: nullableFinite(row.evaluator_runs, `${label}.evaluator_runs`, true),
    control_plane_requests: nullableFinite(row.control_plane_requests, `${label}.control_plane_requests`, true),
    recovery_events: nullableFinite(row.recovery_events, `${label}.recovery_events`, true),
  };
}

function exactSignFlipTwoSidedPValue(diffs: readonly number[]): number | null {
  const n = diffs.length;
  if (n < 1 || n > 18) return null;
  const observedAbsSum = Math.abs(diffs.reduce((a, b) => a + b, 0));
  if (observedAbsSum === 0) return 1;
  const totalConfigs = 1 << n;
  let extreme = 0;
  for (let mask = 0; mask < totalConfigs; mask++) {
    let sum = 0;
    for (let bit = 0; bit < n; bit++) {
      sum += (mask & (1 << bit)) !== 0 ? -diffs[bit]! : diffs[bit]!;
    }
    if (Math.abs(sum) + 1e-12 >= observedAbsSum) extreme++;
  }
  return round6(Math.min(1, extreme / totalConfigs));
}

function binomialSignTwoSidedPValue(wins: number, losses: number): number | null {
  const n = wins + losses;
  if (n < 1) return null;
  const k = Math.min(wins, losses);
  let logHalfPowN = -n * Math.LN2;
  let logChoose = 0;
  let tail = Math.exp(logHalfPowN);
  for (let i = 1; i <= k; i++) {
    logChoose += Math.log(n - i + 1) - Math.log(i);
    tail += Math.exp(logChoose + logHalfPowN);
  }
  return round6(Math.min(1, 2 * tail));
}

interface NormalizedBranch {
  arm_id: string;
  checkpoint_id: string;
  source_state_sha256: string;
  input_revision: string;
  evaluation_ref: string;
  workspace_isolation_id: string;
  terminal_boundary_ref: string;
  decision_context_latest_at: bigint | null;
  first_action_signature: string | null;
  terminal_value: number | null;
  valid_submission: boolean;
  costs: CounterfactualCostVector;
}

function normalizeBranch(value: unknown, label: string): NormalizedBranch {
  const allowed = [
    "arm_id", "checkpoint_id", "source_state_sha256", "input_revision", "evaluation_ref",
    "workspace_isolation_id", "terminal_boundary_ref", "decision_context_latest_at", "first_action_signature",
    "terminal_value", "valid_submission", "costs",
  ];
  const required = [
    "arm_id", "checkpoint_id", "source_state_sha256", "input_revision", "evaluation_ref",
    "workspace_isolation_id", "terminal_boundary_ref", "terminal_value", "valid_submission", "costs",
  ];
  const row = exactObject(value, allowed, required, label);
  if (typeof row.valid_submission !== "boolean") {
    throw new EffectRuntimeRequestError(`${label}.valid_submission requires a boolean`);
  }
  return {
    arm_id: opaqueRef(row.arm_id, `${label}.arm_id`),
    checkpoint_id: opaqueRef(row.checkpoint_id, `${label}.checkpoint_id`),
    source_state_sha256: sha256Hex(row.source_state_sha256, `${label}.source_state_sha256`),
    input_revision: singleLine(row.input_revision, `${label}.input_revision`, 160),
    evaluation_ref: opaqueRef(row.evaluation_ref, `${label}.evaluation_ref`),
    workspace_isolation_id: opaqueRef(row.workspace_isolation_id, `${label}.workspace_isolation_id`),
    terminal_boundary_ref: opaqueRef(row.terminal_boundary_ref, `${label}.terminal_boundary_ref`),
    decision_context_latest_at: parseOptionalTimestamp(row.decision_context_latest_at, `${label}.decision_context_latest_at`),
    first_action_signature: row.first_action_signature === null || row.first_action_signature === undefined
      ? null
      : opaqueRef(row.first_action_signature, `${label}.first_action_signature`),
    terminal_value: nullableFinite(row.terminal_value, `${label}.terminal_value`),
    valid_submission: row.valid_submission,
    costs: normalizeCostVector(row.costs, `${label}.costs`),
  };
}

export function evaluateCounterfactualContinuations(params: JsonObject): CounterfactualContinuationEvaluation {
  const root = exactObject(
    params,
    ["schema_version", "strict_identification", "pairs"],
    ["pairs"],
    "counterfactual continuation evaluation request",
  );
  if (root.schema_version !== undefined && root.schema_version !== COUNTERFACTUAL_CONTINUATION_EVAL_SCHEMA) {
    throw new EffectRuntimeRequestError("unsupported counterfactual continuation schema");
  }
  const strict = root.strict_identification === undefined ? true : Boolean(root.strict_identification);
  if (root.strict_identification !== undefined && typeof root.strict_identification !== "boolean") {
    throw new EffectRuntimeRequestError("strict_identification requires a boolean");
  }
  if (!Array.isArray(root.pairs) || root.pairs.length < 1 || root.pairs.length > 64) {
    throw new EffectRuntimeRequestError("pairs requires 1 to 64 paired continuations");
  }

  const seenPairIds = new Set<string>();
  const acceptedWorkspaces = new Set<string>();
  const acceptedExecutions = new Set<string>();
  const evaluatedPairs: EvaluatedCounterfactualPair[] = [];

  for (let idx = 0; idx < root.pairs.length; idx++) {
    const pairRow = exactObject(
      root.pairs[idx],
      ["pair_id", "task_id", "checkpoint", "treatment", "control"],
      ["pair_id", "task_id", "checkpoint", "treatment", "control"],
      `pairs[${idx}]`,
    );
    const pairId = opaqueRef(pairRow.pair_id, `pairs[${idx}].pair_id`);
    if (seenPairIds.has(pairId)) {
      throw new EffectRuntimeRequestError(`duplicate pair_id: ${pairId}`);
    }
    seenPairIds.add(pairId);
    const taskId = opaqueRef(pairRow.task_id, `pairs[${idx}].task_id`);

    const cpRaw = exactObject(
      pairRow.checkpoint,
      ["checkpoint_id", "source_state_sha256", "input_revision", "evaluation_ref",
        "metric", "unit", "higher_is_better", "comparison_scope", "terminal_boundary_ref",
        "incumbent_at_fork", "fork_recorded_at"],
      ["checkpoint_id", "source_state_sha256", "input_revision", "evaluation_ref",
        "metric", "unit", "higher_is_better", "comparison_scope", "terminal_boundary_ref"],
      `pairs[${idx}].checkpoint`,
    );
    if (typeof cpRaw.higher_is_better !== "boolean") {
      throw new EffectRuntimeRequestError(`pairs[${idx}].checkpoint.higher_is_better requires a boolean`);
    }
    const cpId = opaqueRef(cpRaw.checkpoint_id, `pairs[${idx}].checkpoint.checkpoint_id`);
    const cpSha = sha256Hex(cpRaw.source_state_sha256, `pairs[${idx}].checkpoint.source_state_sha256`);
    const cpInputRev = singleLine(cpRaw.input_revision, `pairs[${idx}].checkpoint.input_revision`, 160);
    const cpEvalRef = opaqueRef(cpRaw.evaluation_ref, `pairs[${idx}].checkpoint.evaluation_ref`);
    const scope: ComparisonScope = {
      metric: singleLine(cpRaw.metric, `pairs[${idx}].checkpoint.metric`, 80),
      unit: singleLine(cpRaw.unit, `pairs[${idx}].checkpoint.unit`, 40),
      higher_is_better: cpRaw.higher_is_better,
      comparison_scope: opaqueRef(cpRaw.comparison_scope, `pairs[${idx}].checkpoint.comparison_scope`),
      input_revision: cpInputRev,
      evaluation_ref: cpEvalRef,
      terminal_boundary_ref: opaqueRef(cpRaw.terminal_boundary_ref, `pairs[${idx}].checkpoint.terminal_boundary_ref`),
    };
    const higherIsBetter = cpRaw.higher_is_better;
    const incumbentAtFork = nullableFinite(cpRaw.incumbent_at_fork, `pairs[${idx}].checkpoint.incumbent_at_fork`);
    const forkAt = parseOptionalTimestamp(cpRaw.fork_recorded_at, `pairs[${idx}].checkpoint.fork_recorded_at`);

    const treatment = normalizeBranch(pairRow.treatment, `pairs[${idx}].treatment`);
    const control = normalizeBranch(pairRow.control, `pairs[${idx}].control`);

    // A row and its workspace labels can change while the underlying arm is the
    // same observation. Arm IDs are execution identities within a frozen fork.
    const executionKey = (armId: string) => JSON.stringify([taskId, cpId, cpSha, armId]);
    const treatmentExecution = executionKey(treatment.arm_id);
    const controlExecution = executionKey(control.arm_id);
    const violations: string[] = [];
    if (treatment.checkpoint_id !== cpId || control.checkpoint_id !== cpId) {
      violations.push("checkpoint_id_mismatch");
    }
    if (treatment.source_state_sha256 !== cpSha || control.source_state_sha256 !== cpSha) {
      violations.push("source_state_sha256_mismatch");
    }
    if (treatment.input_revision !== cpInputRev || control.input_revision !== cpInputRev) {
      violations.push("input_revision_mismatch");
    }
    if (treatment.evaluation_ref !== cpEvalRef || control.evaluation_ref !== cpEvalRef) {
      violations.push("evaluation_ref_mismatch");
    }
    if (treatment.workspace_isolation_id === control.workspace_isolation_id) {
      violations.push("shared_workspace_interference");
    }
    if (acceptedWorkspaces.has(treatment.workspace_isolation_id) || acceptedWorkspaces.has(control.workspace_isolation_id)) {
      violations.push("reused_run_workspace");
    }
    if (treatmentExecution === controlExecution || acceptedExecutions.has(treatmentExecution) ||
        acceptedExecutions.has(controlExecution)) {
      violations.push("reused_arm_execution");
    }
    if (treatment.terminal_boundary_ref !== scope.terminal_boundary_ref ||
        control.terminal_boundary_ref !== scope.terminal_boundary_ref) {
      violations.push("terminal_boundary_mismatch");
    }
    if (forkAt === null || treatment.decision_context_latest_at === null || control.decision_context_latest_at === null) {
      violations.push("missing_context_timing");
    }
    if (forkAt !== null &&
        ((treatment.decision_context_latest_at !== null && treatment.decision_context_latest_at > forkAt) ||
         (control.decision_context_latest_at !== null && control.decision_context_latest_at > forkAt))) {
      violations.push("future_timestamp_context_leakage");
    }
    if (violations.length > 0 && strict) {
      throw new EffectRuntimeRequestError(
        `counterfactual identification failed for ${pairId}: ${violations.join(", ")}`,
      );
    }

    const util = (v: number) => higherIsBetter ? v : -v;
    const actionDiverged = treatment.first_action_signature !== null && control.first_action_signature !== null
      ? treatment.first_action_signature !== control.first_action_signature
      : null;

    const marginalDelta = {} as CounterfactualCostVector;
    const unknownDims: CounterfactualCostDimension[] = [];
    for (const dim of COUNTERFACTUAL_COST_DIMENSIONS) {
      const tc = treatment.costs[dim], cc = control.costs[dim];
      if (tc === null || cc === null) {
        marginalDelta[dim] = null;
        unknownDims.push(dim);
      } else {
        marginalDelta[dim] = round6(tc - cc);
      }
    }

    const validPair = violations.length === 0;
    if (validPair) {
      acceptedWorkspaces.add(treatment.workspace_isolation_id);
      acceptedWorkspaces.add(control.workspace_isolation_id);
      acceptedExecutions.add(treatmentExecution);
      acceptedExecutions.add(controlExecution);
    }
    const bothScored = validPair && treatment.valid_submission && control.valid_submission
      && treatment.terminal_value !== null && control.terminal_value !== null;

    let rawDelta: number | null = null;
    let tauB: number | null = null;
    let tGain: number | null = null;
    let cGain: number | null = null;
    let outcomeClass: CounterfactualPairOutcome;

    if (!validPair) {
      outcomeClass = violations.length === 1 && violations[0] === "missing_context_timing"
        ? "unscored_or_incomplete" : "contaminated_excluded";
    } else if (!bothScored) {
      outcomeClass = "unscored_or_incomplete";
    } else {
      rawDelta = round6(treatment.terminal_value! - control.terminal_value!);
      tauB = round6(util(treatment.terminal_value!) - util(control.terminal_value!));
      if (incumbentAtFork !== null) {
        tGain = round6(util(treatment.terminal_value!) - util(incumbentAtFork));
        cGain = round6(util(control.terminal_value!) - util(incumbentAtFork));
      }
      if (actionDiverged === null) outcomeClass = "action_unobserved";
      else if (actionDiverged === false) outcomeClass = "action_identical";
      else if (tauB > 0) outcomeClass = "diverged_with_gain";
      else if (tauB === 0) outcomeClass = "diverged_no_gain";
      else outcomeClass = "diverged_regressed";
    }

    evaluatedPairs.push({
      pair_id: pairId,
      task_id: taskId,
      checkpoint_id: cpId,
      scope,
      identification_valid: validPair,
      identification_violations: violations,
      outcome_classification: outcomeClass,
      action_diverged: actionDiverged,
      treatment_arm_id: treatment.arm_id,
      control_arm_id: control.arm_id,
      treatment_terminal_value: treatment.terminal_value,
      control_terminal_value: control.terminal_value,
      raw_terminal_delta: rawDelta,
      tau_b_direction_normalized: tauB,
      treatment_gain_over_incumbent: tGain,
      control_gain_over_incumbent: cGain,
      marginal_cost_delta: marginalDelta,
      unknown_cost_dimensions: unknownDims,
    });
  }

  const validRows = evaluatedPairs.filter(p => p.identification_valid);
  const scoredRows = validRows.filter(p => p.tau_b_direction_normalized !== null);
  const tauValues = scoredRows.map(p => p.tau_b_direction_normalized!);
  const wins = tauValues.filter(v => v > 0).length;
  const ties = tauValues.filter(v => v === 0).length;
  const losses = tauValues.filter(v => v < 0).length;
  const scopeCount = new Set(scoredRows.map(row => JSON.stringify(row.scope))).size;
  const comparableTaus = scopeCount === 1 ? tauValues : [];

  const deltaSums = Object.fromEntries(COUNTERFACTUAL_COST_DIMENSIONS.map(d => [d, 0])) as Record<CounterfactualCostDimension, number>;
  const observedCounts = Object.fromEntries(COUNTERFACTUAL_COST_DIMENSIONS.map(d => [d, 0])) as Record<CounterfactualCostDimension, number>;
  const missingCounts = Object.fromEntries(COUNTERFACTUAL_COST_DIMENSIONS.map(d => [d, 0])) as Record<CounterfactualCostDimension, number>;

  for (const row of validRows) {
    for (const dim of COUNTERFACTUAL_COST_DIMENSIONS) {
      const val = row.marginal_cost_delta[dim];
      if (val === null) missingCounts[dim]++;
      else {
        deltaSums[dim] = round6(deltaSums[dim] + val);
        observedCounts[dim]++;
      }
    }
  }

  const taskMap = new Map<string, EvaluatedCounterfactualPair[]>();
  for (const row of validRows) {
    const group = JSON.stringify([row.task_id, row.scope]);
    const list = taskMap.get(group) ?? [];
    list.push(row);
    taskMap.set(group, list);
  }
  const byTask = [...taskMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, rows]) => {
      const taus = rows.filter(r => r.tau_b_direction_normalized !== null).map(r => r.tau_b_direction_normalized!);
      return {
        task_id: rows[0]!.task_id,
        scope: rows[0]!.scope,
        valid_pairs: rows.length,
        scored_pairs: taus.length,
        mean_tau_b: taus.length ? round6(taus.reduce((a, b) => a + b, 0) / taus.length) : null,
        wins: taus.filter(v => v > 0).length,
        ties: taus.filter(v => v === 0).length,
        losses: taus.filter(v => v < 0).length,
      };
    });

  return assertMethodologyOperatorOutput("counterfactual_continuation", {
    schema_version: COUNTERFACTUAL_CONTINUATION_EVAL_SCHEMA,
    strict_identification: strict,
    all_pairs_identification_valid: evaluatedPairs.every(p => p.identification_valid),
    counts: {
      total_pairs: evaluatedPairs.length,
      valid_pairs: validRows.length,
      contaminated_pairs: evaluatedPairs.length - validRows.length,
      scored_valid_pairs: scoredRows.length,
      unscored_valid_pairs: validRows.length - scoredRows.length,
      action_diverged_pairs: validRows.filter(p => p.action_diverged === true).length,
      action_identical_pairs: validRows.filter(p => p.action_diverged === false).length,
      action_unobserved_pairs: validRows.filter(p => p.action_diverged === null).length,
      wins,
      ties,
      losses,
    },
    estimand_summary: {
      comparable_scope_count: scopeCount,
      mean_tau_b: comparableTaus.length ? round6(comparableTaus.reduce((a, b) => a + b, 0) / comparableTaus.length) : null,
      median_tau_b: comparableTaus.length ? round6(median(comparableTaus)) : null,
      min_tau_b: comparableTaus.length ? round6(Math.min(...comparableTaus)) : null,
      max_tau_b: comparableTaus.length ? round6(Math.max(...comparableTaus)) : null,
      exact_sign_flip_p_value: exactSignFlipTwoSidedPValue(comparableTaus),
      binomial_sign_p_value: scopeCount === 1 ? binomialSignTwoSidedPValue(wins, losses) : null,
    },
    complete_marginal_costs: {
      observed_delta_sums: deltaSums,
      observed_pair_counts: observedCounts,
      missing_pair_counts: missingCounts,
    },
    by_task: byTask,
    pairs: evaluatedPairs,
    advisory_only: true,
    grants_execution_authority: false,
  }, 262_144) as unknown as CounterfactualContinuationEvaluation;
}
