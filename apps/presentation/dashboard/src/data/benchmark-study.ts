import { z } from "zod";

const metricValueSchema = z.object({
  value: z.number().finite(),
  total: z.number().finite().nonnegative().optional(),
  unit: z.string().optional(),
  higher_is_better: z.boolean(),
}).passthrough();

const effortSchema = z.record(z.string(), z.number().finite()).default({});

const insightSchema = z.object({
  outcome_status: z.string().optional(),
  failure_class: z.string(),
  causal_summary: z.string(),
  expectedness: z.string(),
  implication: z.string(),
  next_probe: z.string(),
  confidence: z.string(),
  evidence_refs: z.array(z.string()).optional(),
}).passthrough();

const armCellSchema = z.object({
  arm_id: z.string(),
  selected_run_id: z.string().nullable(),
  score_countable: z.boolean(),
  metrics: z.record(z.string(), metricValueSchema),
  effort: effortSchema,
  insight: insightSchema.nullable().optional(),
});

const runSchema = z.object({
  run_id: z.string(),
  case_id: z.string(),
  arm_id: z.string(),
  arm_role: z.string(),
  status: z.string(),
  protocol_id: z.string(),
  runner_revision: z.string().optional(),
  observed_at: z.string(),
  metrics: z.record(z.string(), metricValueSchema),
  countability: z.object({
    integrity_qualified: z.boolean(),
    official_result_present: z.boolean(),
    score_countable: z.boolean(),
  }).passthrough(),
  treatment_fidelity: z.string(),
  effort: effortSchema,
  redacted_insight: insightSchema.nullable().optional(),
  upload_provenance: z.object({
    producer_id: z.string(),
    producer_version: z.string(),
    observed_at: z.string(),
    source_revision: z.string(),
  }).passthrough(),
}).passthrough();

const metricAggregateSchema = z.object({
  case_denominator: z.number().int().nonnegative(),
  value_sum: z.number().finite(),
  value_mean: z.number().finite().nullable(),
  value_median: z.number().finite().nullable(),
  value_min: z.number().finite().nullable(),
  value_max: z.number().finite().nullable(),
  case_macro_rate: z.number().finite().optional(),
  suite_micro_rate: z.number().finite().optional(),
  suite_micro_numerator: z.number().finite().optional(),
  suite_micro_denominator: z.number().finite().positive().optional(),
}).passthrough();

const armSchema = z.object({
  arm_id: z.string(),
  arm_role: z.string(),
  factor_assignments: z.record(z.string(), z.string()),
  protocol_counts: z.record(z.string(), z.number().int().nonnegative()).default({}),
  runner_revision_counts: z.record(z.string(), z.number().int().nonnegative()).default({}),
  orchestrator_runtime_counts: z.record(z.string(), z.number().int().nonnegative()).default({}),
  intended_case_count: z.number().int().positive(),
  run_count: z.number().int().nonnegative(),
  terminal_run_count: z.number().int().nonnegative(),
  selected_score_countable_case_count: z.number().int().nonnegative(),
  coverage_rate: z.number().finite().min(0).max(1),
  metrics: z.record(z.string(), metricAggregateSchema),
  binary_outcomes: z.record(z.string(), z.object({
    success_count: z.number().int().nonnegative(),
    case_denominator: z.number().int().nonnegative(),
    success_rate: z.number().finite().min(0).max(1).nullable(),
  })),
  effort: z.record(z.string(), z.object({
    denominator: z.number().int().nonnegative(),
    mean: z.number().finite().nullable(),
    median: z.number().finite().nullable(),
  })),
  failure_class_counts: z.record(z.string(), z.number().int().nonnegative()),
}).passthrough();

const metricDeltaSchema = z.object({
  baseline_value: z.number().finite(),
  candidate_value: z.number().finite(),
  delta: z.number().finite().optional(),
  delta_rate: z.number().finite().optional(),
  comparison_unavailable_reason: z.string().optional(),
  direction: z.enum(["improved", "flat", "regressed"]).optional(),
}).passthrough().refine((metric) => metric.comparison_unavailable_reason
  ? metric.delta === undefined && metric.delta_rate === undefined && metric.direction === undefined
  : metric.delta !== undefined, "Metric comparison must contain a delta or an unavailable reason");

const matchedComparisonSchema = z.object({
  comparison_id: z.string(),
  comparison_anchor_run_id: z.string(),
  candidate_run_id: z.string(),
  candidate_arm_id: z.string(),
  primary_metric: z.string(),
  matched_pair_countable: z.literal(true),
  metric_deltas: z.record(z.string(), metricDeltaSchema),
}).passthrough();

export const benchmarkStudyDashboardSchema = z.object({
  ok: z.literal(true),
  schema_version: z.literal("benchmark_study_dashboard_v0"),
  benchmark_id: z.string(),
  study_id: z.string(),
  status: z.enum(["complete", "provisional"]),
  design: z.object({
    protocol_id: z.string(),
    comparison_protocol_id: z.string(),
    baseline_arm_id: z.string(),
    case_set: z.object({
      case_set_id: z.string(),
      case_ids: z.array(z.string()),
    }),
    metric_catalog: z.array(z.object({
      metric_name: z.string(),
      role: z.enum(["primary", "guardrail", "supporting"]),
      unit: z.string().optional(),
      higher_is_better: z.boolean(),
      binary: z.boolean(),
    })),
    labels: z.record(z.string(), z.string()),
  }).passthrough(),
  campaign: z.object({
    intended_case_count: z.number().int().positive(),
    intended_arm_count: z.number().int().positive(),
    intended_cell_denominator: z.number().int().positive(),
    selected_score_countable_cell_count: z.number().int().nonnegative(),
    selected_score_countable_coverage_rate: z.number().finite().min(0).max(1),
    complete_declared_design_case_count: z.number().int().nonnegative(),
    ambiguous_score_countable_cell_count: z.number().int().nonnegative(),
    in_flight_run_count: z.number().int().nonnegative(),
    matched_pair_countable_count: z.number().int().nonnegative(),
    factorial_contrast_count: z.number().int().nonnegative(),
    factorial_contrast_countable_count: z.number().int().nonnegative(),
    runtime_observation_count: z.number().int().nonnegative(),
    runtime_classification_counts: z.record(z.string(), z.number().int().nonnegative()),
  }),
  arms: z.array(armSchema),
  contrasts: z.record(z.string(), z.object({
    matched_pair_denominator: z.number().int().nonnegative(),
    primary_metric_directions: z.object({
      improved: z.number().int().nonnegative(),
      flat: z.number().int().nonnegative(),
      regressed: z.number().int().nonnegative(),
    }),
    binary_metric_transitions: z.record(z.string(), z.object({
      "0_to_1": z.number().int().nonnegative(),
      "1_to_0": z.number().int().nonnegative(),
      same: z.number().int().nonnegative(),
    })),
  })),
  cases: z.array(z.object({
    case_id: z.string(),
    complete_declared_design: z.boolean(),
    arms: z.array(armCellSchema),
    eligible_comparisons: z.array(matchedComparisonSchema),
    largest_eligible_primary_contrast: matchedComparisonSchema.nullable(),
  })),
  runs: z.array(runSchema),
  authority: z.object({
    score_source: z.string(),
    matched_comparison_source: z.string(),
    factorial_comparison_source: z.string().nullable(),
    manifest_changes_scores: z.literal(false),
    dashboard_is_execution_authority: z.literal(false),
  }),
  public_boundary: z.object({
    raw_task_recorded: z.literal(false),
    raw_trajectory_recorded: z.literal(false),
    hidden_evaluation_recorded: z.literal(false),
    raw_verifier_output_recorded: z.literal(false),
    credentials_recorded: z.literal(false),
    local_paths_recorded: z.literal(false),
  }),
  write_performed: z.literal(false),
  network_access_performed: z.literal(false),
});

export type BenchmarkStudyDashboard = z.infer<typeof benchmarkStudyDashboardSchema>;
export type BenchmarkStudyArm = BenchmarkStudyDashboard["arms"][number];
export type BenchmarkStudyCase = BenchmarkStudyDashboard["cases"][number];
export type BenchmarkStudyRun = BenchmarkStudyDashboard["runs"][number];
export type BenchmarkStudyView = "campaign" | "arms" | "cases" | "runs";

export function selectBenchmarkStudyRun(
  runs: BenchmarkStudyRun[], runId: string, caseId: string,
): BenchmarkStudyRun | undefined {
  if (!runId && !caseId) return runs[0];
  const matches = runs.filter((run) => run.run_id === runId && (!caseId || run.case_id === caseId));
  // Legacy runId-only bookmarks are safe only when they identify exactly one run.
  return matches.length === 1 ? matches[0] : undefined;
}

export function parseBenchmarkStudyDashboard(value: unknown): BenchmarkStudyDashboard {
  return benchmarkStudyDashboardSchema.parse(value);
}

export function resolveBenchmarkStudyDashboardUrl(value: string, baseUrl: string): string {
  const base = new URL(baseUrl);
  const resolved = new URL(value || "/benchmark-study.example.json", base);
  if (!new Set(["http:", "https:"]).has(resolved.protocol)) {
    throw new Error("Benchmark dashboard source must use HTTP or HTTPS");
  }
  if (resolved.origin !== base.origin) {
    throw new Error("Benchmark dashboard source must use same-origin local readback");
  }
  return resolved.toString();
}
