import { z } from "zod";

import type { PresentationSurface } from "./status";

const researchDecisionSchema = z.enum([
  "selected",
  "rejected",
  "insufficient_evidence",
  "blocked",
  "superseded",
]);

const researchConfidenceSchema = z.enum(["high", "medium", "low"]);
const researchToneSchema = z.enum(["neutral", "success", "warning", "info", "danger"]);
const researchLayerStateSchema = z.enum([
  "supported",
  "partial",
  "rejected",
  "insufficient_evidence",
  "blocked",
  "pending",
]);
const researchGateStateSchema = z.enum([
  "passed",
  "failed",
  "pending",
  "blocked",
  "insufficient_evidence",
  "partial",
]);
const researchEventStateSchema = z.enum([
  "pending",
  "partially_adjudicated",
  "selected",
  "rejected",
  "insufficient_evidence",
  "blocked",
  "superseded",
]);

const researchMetricSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  value: z.string().min(1),
  detail: z.string().min(1),
  tone: researchToneSchema,
}).strict();

const sourcePeriodMetricSchema = z.object({
  metric_id: z.string().min(1),
  label: z.string().min(1),
  event_namespace: z.string().min(1),
  event_id: z.string().min(1),
  event_at: z.string().min(1),
  instrument_id: z.string().min(1),
  scope_id: z.string().min(1),
  period_start: z.string().min(1),
  period_end: z.string().min(1),
  source_state: z.enum(["ok", "error"]),
  value: z.number().finite().nullable(),
  unit: z.string().min(1),
  metric_basis: z.enum([
    "realized_cash",
    "period_estimate",
    "annualized_estimate",
  ]),
  metric_semantics: z.enum([
    "generic",
    "entry_price",
    "cash_delta",
    "cumulative_funding_cost",
    "fill_fee",
    "account_nav",
    "account_component",
    "withdrawable",
    "external_asset_coverage",
  ]),
  value_origin: z.enum(["source_reported", "derived"]),
  value_precision: z.enum(["exact", "rounded"]),
  observation_authority: z.enum([
    "fill_vwap",
    "source_reported_exact",
    "derived_exact",
    "source_reported_rounded",
    "rounded_position_entry",
    "derived_rounded",
  ]),
  sign_basis: z.enum([
    "not_signed",
    "account_cash_change",
    "funding_cost",
    "fee_cost",
  ]),
  fee_inclusion: z.enum(["not_applicable", "builder_included"]),
  account_scope: z.enum([
    "not_applicable",
    "product",
    "venue",
    "unified_account",
    "external_asset",
  ]),
  account_value_role: z.enum([
    "not_applicable",
    "nav_owner",
    "composition",
    "reconciliation",
    "withdrawable",
    "coverage",
  ]),
  includes_isolated_margin: z.boolean(),
  expected_components: z.array(z.string().min(1)).min(1).max(32),
  observed_components: z.array(z.string().min(1)).max(32),
  double_counted_components: z.array(z.string().min(1)).max(32),
  numerator_scope: z.array(z.string().min(1)).max(32),
  denominator_scope: z.array(z.string().min(1)).max(32),
  lineage_id: z.string().min(1),
  source_ref: z.string().min(1),
  methodology_state: z.enum([
    "verified",
    "declared_only",
    "unverified",
    "conflicting",
  ]),
  anomaly_state: z.enum(["clear", "unverified", "confirmed"]),
  event_identity: z.object({
    namespace: z.string().min(1),
    source_event_id: z.string().min(1),
    event_at: z.string().min(1),
    instrument_id: z.string().min(1),
    scope_id: z.string().min(1),
  }).strict(),
  coverage_state: z.enum(["complete", "partial", "missing", "source_error"]),
  missing_components: z.array(z.string().min(1)).max(32),
  lineage_state: z.enum(["primary", "duplicate_upstream"]),
  duplicate_of: z.string().min(1).nullable(),
  independent_evidence: z.boolean(),
  gap_reasons: z.array(z.string().min(1)).max(40),
  account_nav_treatment: z.enum([
    "authoritative_total",
    "composition_only",
    "reconciliation_only",
    "venue_liquidity_only",
    "external_asset_coverage_only",
    "not_account_value",
  ]),
  ready_eligible: z.literal(false),
  admission_reason: z.literal("source_period_metric_is_evidence_only"),
}).strict();

const spotMarketIdentitySchema = z.object({
  pairs: z.array(z.object({
    name: z.string().min(1),
    asset_indexes: z.array(z.number().int().nonnegative()).length(2),
    is_canonical: z.boolean(),
    source_ref: z.string().min(1),
  }).strict()).max(64),
  tokens: z.array(z.object({
    index: z.number().int().nonnegative(),
    symbol: z.string().min(1),
    source_ref: z.string().min(1),
  }).strict()).max(128),
  contexts: z.array(z.object({
    coin: z.string().min(1),
    observed_at: z.string().min(1),
    mark_price: z.number().finite().positive().nullable(),
    source_ref: z.string().min(1),
  }).strict()).max(64),
  markets: z.array(z.object({
    pair_name: z.string().min(1),
    context_coin: z.string().min(1),
    base_asset: z.object({
      index: z.number().int().nonnegative(),
      symbol: z.string().min(1),
    }).strict(),
    quote_asset: z.object({
      index: z.number().int().nonnegative(),
      symbol: z.string().min(1),
    }).strict(),
    observed_at: z.string().min(1),
    mark_price: z.number().finite().positive().nullable(),
    canonicality: z.enum(["canonical_name", "noncanonical_name"]),
    backing_inference: z.literal("not_inferred"),
    source_refs: z.array(z.string().min(1)).length(4),
    ready_eligible: z.literal(false),
  }).strict()).max(64),
}).strict();

const researchSummarySchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  title: z.string().min(1),
  summary: z.string().min(1),
  tone: researchToneSchema,
  destination_anchor: z.string().regex(/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/),
}).strict();

const researchLayerSchema = z.object({
  id: z.string().min(1),
  order: z.number().int().positive(),
  label: z.string().min(1),
  status: researchLayerStateSchema,
  summary: z.string().min(1),
  evidence_points: z.array(z.string().min(1)).min(1).max(12),
}).strict();

const researchObservationSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  kind: z.enum([
    "observation_range",
    "current_fact",
    "historical_fact",
    "management_guidance",
    "analyst_estimate",
    "agent_inference",
  ]),
  value: z.string().min(1),
  as_of: z.string().min(1),
  source_ref: z.string().min(1),
  source_type: z.enum([
    "company_filing",
    "company_guidance",
    "regulator",
    "exchange",
    "market_data",
    "industry_source",
    "high_quality_media",
    "community_signal",
    "agent_analysis",
  ]),
  confidence: researchConfidenceSchema,
  invalidation: z.string().min(1),
}).strict();

const researchScenarioSchema = z.object({
  scenario: z.enum(["bull", "base", "bear"]),
  label: z.string().min(1),
  value: z.string().min(1),
  horizon: z.string().min(1),
  probability: z.number().min(0).max(1),
  assumptions: z.array(z.string().min(1)).min(1).max(8),
}).strict();

const researchEntitySchema = z.object({
  entity_id: z.string().min(1),
  symbol: z.string().min(1),
  display_name: z.string().min(1),
  classification: z.string().min(1),
  status: researchDecisionSchema,
  confidence: researchConfidenceSchema,
  inference: z.string().min(1),
  observations: z.array(researchObservationSchema).min(1).max(20),
  scenario_estimates: z.array(researchScenarioSchema).length(3),
  counterevidence: z.array(z.string().min(1)).min(1).max(12),
  thesis_breakers: z.array(z.string().min(1)).min(1).max(12),
  next_events: z.array(z.string().min(1)).max(12),
}).strict().superRefine((entity, context) => {
  const scenarioNames = new Set(entity.scenario_estimates.map((item) => item.scenario));
  if (
    scenarioNames.size !== 3
    || !scenarioNames.has("bull")
    || !scenarioNames.has("base")
    || !scenarioNames.has("bear")
  ) {
    context.addIssue({
      code: "custom",
      message: "scenario_estimates must contain bull, base and bear",
      path: ["scenario_estimates"],
    });
  }
  const probability = entity.scenario_estimates.reduce(
    (sum, item) => sum + item.probability,
    0,
  );
  if (Math.abs(probability - 1) > 0.000001) {
    context.addIssue({
      code: "custom",
      message: "scenario_estimates probabilities must sum to 1",
      path: ["scenario_estimates"],
    });
  }
});

const researchLedgerSchema = z.object({
  case_id: z.string().min(1),
  label: z.string().min(1),
  gate_states: z.array(z.object({
    gate_id: z.string().min(1),
    label: z.string().min(1),
    status: researchGateStateSchema,
    summary: z.string().min(1),
  }).strict()).min(1).max(16),
  decision: researchDecisionSchema,
  summary: z.string().min(1),
  evidence_refs: z.array(z.string().min(1)).min(1).max(20),
}).strict();

const researchArtifactSchema = z.object({
  artifact_id: z.string().min(1),
  kind: z.enum([
    "research_packet",
    "evidence_matrix",
    "event_gate_packet",
    "methodology_note",
  ]),
  label: z.string().min(1),
  summary: z.string().min(1),
  artifact_ref: z.string().min(1),
  evidence_refs: z.array(z.string().min(1)).min(1).max(20),
}).strict();

const researchEventGateSchema = z.object({
  event_id: z.string().min(1),
  label: z.string().min(1),
  status: researchEventStateSchema,
  observation_window: z.string().min(1),
  frozen_hypothesis: z.string().min(1),
  observables: z.array(z.string().min(1)).min(1).max(16),
  current_evidence: z.array(z.string().min(1)).max(16),
  supports: z.array(z.string().min(1)).min(1).max(12),
  refutes: z.array(z.string().min(1)).min(1).max(12),
  thesis_breakers: z.array(z.string().min(1)).min(1).max(12),
  next_review: z.string().min(1),
}).strict();

export const decisionResearchViewSchema = z.object({
  identity: z.object({
    title: z.string().min(1),
    subtitle: z.string().min(1),
    as_of: z.string().min(1),
    evidence_cutoff: z.string().min(1),
  }).strict(),
  adjudication: z.object({
    status: researchDecisionSchema,
    label: z.string().min(1),
    summary: z.string().min(1),
    confidence: researchConfidenceSchema,
  }).strict(),
  metrics: z.array(researchMetricSchema).min(1).max(12),
  source_period_metrics: z.array(sourcePeriodMetricSchema).max(24).optional().default([]),
  spot_market_identity: spotMarketIdentitySchema.optional(),
  dashboard_summaries: z.array(researchSummarySchema).max(3),
  layers: z.array(researchLayerSchema).min(1).max(12),
  entities: z.array(researchEntitySchema).max(24),
  research_ledger: z.array(researchLedgerSchema).max(40),
  artifacts: z.array(researchArtifactSchema).max(20).optional().default([]),
  event_gates: z.array(researchEventGateSchema).max(32),
  method_state: z.object({
    revision: z.string().min(1),
    lifecycle_state: z.string().min(1),
    active_method_changed: z.boolean(),
    summary: z.string().min(1),
  }).strict(),
  boundary: z.object({
    research_aid_only: z.literal(true),
    investment_advice: z.literal(false),
    trading_allowed: z.literal(false),
    raw_provider_payload_recorded: z.literal(false),
    private_source_content_read: z.literal(false),
  }).strict(),
}).strict();

export const presentationProjectionEnvelopeSchema = z.object({
  schema_version: z.literal("extension_projection_surface_v0"),
  extension_id: z.string().min(1),
  extension_revision: z.string().min(1),
  surface_id: z.string().min(1),
  surface_kind: z.string().min(1),
  view_schema: z.literal("decision_research_dashboard_v0"),
  visibility: z.literal("public-safe"),
  goal_id: z.string().min(1),
  generated_at: z.string().min(1),
  review_due_at: z.string().min(1).nullable(),
  lineage: z.record(z.string(), z.unknown()).optional(),
  view: decisionResearchViewSchema,
  payload_sha256: z.string().regex(/^[0-9a-f]{64}$/),
}).strict();

export const presentationProjectionResponseSchema = z.object({
  ok: z.literal(true),
  projection: presentationProjectionEnvelopeSchema,
}).strict();

export type DecisionResearchView = z.infer<typeof decisionResearchViewSchema>;
export type PresentationProjectionEnvelope = z.infer<
  typeof presentationProjectionEnvelopeSchema
>;

export function parsePresentationProjection(
  payload: unknown,
): PresentationProjectionEnvelope {
  return presentationProjectionResponseSchema.parse(payload).projection;
}

export async function fetchPresentationProjection(
  detailUrl: string,
  surface: PresentationSurface,
): Promise<PresentationProjectionEnvelope> {
  if (surface.state !== "ready" && surface.state !== "review_due") {
    throw new Error("surface has no published projection to fetch");
  }
  if (surface.visibility !== "public-safe") {
    throw new Error("rich projection rendering requires public-safe visibility");
  }
  const target = new URL(detailUrl);
  if (
    !["http:", "https:"].includes(target.protocol)
    || !["127.0.0.1", "localhost", "::1", "[::1]"].includes(target.hostname)
  ) {
    throw new Error("projection detail URL must use a loopback host");
  }
  target.searchParams.set("extension_id", surface.detail_ref.extension_id);
  target.searchParams.set("surface_id", surface.detail_ref.surface_id);
  target.searchParams.set("extension_revision", surface.detail_ref.extension_revision);
  target.searchParams.set("payload_sha256", surface.detail_ref.payload_sha256);
  const response = await fetch(target.toString(), { cache: "no-store" });
  if (!response.ok) {
    let detail = "";
    try {
      const failure: unknown = await response.json();
      if (typeof failure === "object" && failure !== null && "error" in failure && typeof failure.error === "string") {
        detail = `: ${failure.error.slice(0, 300)}`;
      }
    } catch { /* A malformed error response still clears the previous view. */ }
    throw new Error(`HTTP ${response.status} while loading projection${detail}`);
  }
  const projection = parsePresentationProjection(await response.json());
  const expectedIdentity = surface.detail_ref;
  if (
    projection.extension_id !== expectedIdentity.extension_id
    || projection.surface_id !== expectedIdentity.surface_id
    || projection.extension_revision !== expectedIdentity.extension_revision
    || projection.payload_sha256 !== expectedIdentity.payload_sha256
    || projection.view_schema !== surface.view_schema
    || projection.surface_kind !== surface.surface_kind
    || projection.goal_id !== surface.goal_id
  ) {
    throw new Error("projection response does not match the requested detail_ref");
  }
  return projection;
}
