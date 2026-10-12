import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";

import { fetchPresentationProjection, parsePresentationProjection, publishedResearchLineage, type DecisionResearchView } from "../src/data/decision-research";
import projectionFixture from "../src/data/fixtures/presentation-projection.example.json";
import { presentationSurfaceSchema } from "../src/data/status";
import {
  SourcePeriodMetricsCard,
  SpotMarketIdentityCard,
  DecisionResearchSurface,
} from "../src/views/decision-research-surface";

type SourcePeriodMetric = DecisionResearchView["source_period_metrics"][number];
type SpotMarketIdentity = NonNullable<DecisionResearchView["spot_market_identity"]>;

const periodMetric: SourcePeriodMetric = {
  metric_id: "synthetic-fees",
  label: "Synthetic fees",
  event_namespace: "synthetic.period.metric",
  event_id: "event-2026w01",
  event_at: "2026-01-07T23:00:00Z",
  instrument_id: "SYNTH-USD",
  scope_id: "synthetic-scope",
  period_start: "2026-01-01",
  period_end: "2026-01-07",
  source_state: "ok",
  value: 125,
  unit: "USD",
  metric_basis: "period_estimate",
  metric_semantics: "generic",
  value_origin: "derived",
  value_precision: "exact",
  observation_authority: "derived_exact",
  sign_basis: "not_signed",
  fee_inclusion: "not_applicable",
  account_scope: "not_applicable",
  account_value_role: "not_applicable",
  includes_isolated_margin: false,
  expected_components: ["core", "secondary"],
  observed_components: ["core"],
  double_counted_components: ["secondary"],
  numerator_scope: ["core"],
  denominator_scope: [],
  lineage_id: "synthetic-upstream-week",
  source_ref: "source:synthetic-fees",
  methodology_state: "verified",
  anomaly_state: "unverified",
  event_identity: {
    namespace: "synthetic.period.metric",
    source_event_id: "event-2026w01",
    event_at: "2026-01-07T23:00:00Z",
    instrument_id: "SYNTH-USD",
    scope_id: "synthetic-scope",
  },
  coverage_state: "partial",
  missing_components: ["secondary"],
  lineage_state: "primary",
  duplicate_of: null,
  independent_evidence: true,
  gap_reasons: ["anomaly:unverified"],
  account_nav_treatment: "not_account_value",
  ready_eligible: false,
  admission_reason: "source_period_metric_is_evidence_only",
};

assert.equal(renderToStaticMarkup(<SourcePeriodMetricsCard metrics={[]} />), "");
const metricsMarkup = renderToStaticMarkup(
  <SourcePeriodMetricsCard
    metrics={[
      periodMetric,
      {
        ...periodMetric,
        metric_id: "synthetic-missing",
        period_start: "2026-01-08",
        period_end: "2026-01-08",
        value: null,
        observed_components: [],
        double_counted_components: [],
        numerator_scope: [],
        denominator_scope: ["capital"],
        coverage_state: "missing",
        independent_evidence: false,
        gap_reasons: [],
      },
    ]}
  />,
);
assert.match(metricsMarkup, /125 USD/);
assert.match(metricsMarkup, /2026-01-01 → 2026-01-07/);
assert.match(metricsMarkup, /missing \(not zero\)/);
assert.match(metricsMarkup, /Excluded double-counted components:.*secondary/);
assert.match(metricsMarkup, /Holds:.*anomaly:unverified/);
assert.match(metricsMarkup, /capital/);

const spotIdentity: SpotMarketIdentity = {
  pairs: [],
  tokens: [],
  contexts: [],
  markets: [
    {
      pair_name: "SYNTH-SPOT",
      context_coin: "SYNTH-SPOT",
      base_asset: { index: 3, symbol: "SYN" },
      quote_asset: { index: 0, symbol: "USDC" },
      observed_at: "2026-01-15T12:00:00Z",
      mark_price: 4.25,
      canonicality: "noncanonical_name",
      backing_inference: "not_inferred",
      source_refs: ["source:pair", "source:base", "source:quote", "source:context"],
      ready_eligible: false,
    },
    {
      pair_name: "MISSING-SPOT",
      context_coin: "MISSING-SPOT",
      base_asset: { index: 4, symbol: "MISS" },
      quote_asset: { index: 0, symbol: "USDC" },
      observed_at: "2026-01-15T12:00:00Z",
      mark_price: null,
      canonicality: "canonical_name",
      backing_inference: "not_inferred",
      source_refs: ["source:pair-2", "source:base-2", "source:quote", "source:context-2"],
      ready_eligible: false,
    },
  ],
};

assert.equal(renderToStaticMarkup(<SpotMarketIdentityCard identity={null} />), "");
assert.equal(
  renderToStaticMarkup(<SpotMarketIdentityCard identity={{ ...spotIdentity, markets: [] }} />),
  "",
);
const spotMarkup = renderToStaticMarkup(
  <SpotMarketIdentityCard identity={spotIdentity} />,
);
assert.match(spotMarkup, /SYN \/ USDC/);
assert.match(spotMarkup, /4.25/);
assert.match(spotMarkup, /missing \(not zero\)/);

const projection = parsePresentationProjection(projectionFixture);
const surface = presentationSurfaceSchema.parse({
  extension_id: projection.extension_id, extension_revision: projection.extension_revision,
  surface_id: projection.surface_id, surface_kind: projection.surface_kind,
  title: "Synthetic research", view_schema: projection.view_schema, visibility: "public-safe",
  state: "review_due", goal_id: projection.goal_id, generated_at: projection.generated_at,
  review_due_at: projection.review_due_at, diagnostic: null,
  empty_state_title: "No research", empty_state_detail: "No projection",
  detail_ref: {extension_id: projection.extension_id, surface_id: projection.surface_id,
    extension_revision: projection.extension_revision, payload_sha256: projection.payload_sha256},
});
const markup = renderToStaticMarkup(<DecisionResearchSurface goals={[]} surface={surface}
  view={projection.view} projection={{...projection, lineage: {...projection.lineage,
    supersedes: ["synthetic-previous-source"]}}} />);
for (const value of [projection.goal_id, projection.generated_at, projection.review_due_at!,
  "synthetic-research-2026-01-15", "Source version", "synthetic-previous-source",
  projection.extension_revision, projection.payload_sha256, "does not extend the review deadline"]) {
  assert(markup.includes(value), `Published reference must retain ${value}`);
}
assert.match(markup, /<details[^>]*data-testid="research-reference-details"/);
assert.deepEqual(publishedResearchLineage(), {sourceId: null, version: null, supersedes: null});
assert.deepEqual(publishedResearchLineage({...projection, lineage: {source_id: false, version: "1", supersedes: false}}),
  {sourceId: null, version: null, supersedes: null});
const legacy = {...projection, review_due_at: null, lineage: undefined};
const legacyMarkup = renderToStaticMarkup(<DecisionResearchSurface goals={[]}
  surface={{...surface, review_due_at: null}} view={legacy.view} projection={legacy} zh />);
assert.match(legacyMarkup, /原复核期限/);
assert.match(legacyMarkup, /未知/);
assert(!legacyMarkup.includes("synthetic-research-2026-01-15"), "Absent lineage must not be guessed from the view");

const originalFetch = globalThis.fetch;
let reply = projection;
globalThis.fetch = async () => new Response(JSON.stringify({ok: true, projection: reply}));
try {
  assert.deepEqual(await fetchPresentationProjection("http://localhost/extension-projection", surface), projection);
  for (const changes of [
    {payload_sha256: "0".repeat(64)}, {extension_revision: "old-revision"},
    {goal_id: "other-goal"}, {generated_at: "2026-01-16T12:00:00Z"},
    {review_due_at: "2026-03-15T12:00:00Z"},
  ]) {
    reply = {...projection, ...changes};
    await assert.rejects(fetchPresentationProjection("http://localhost/extension-projection", surface),
      /does not match the requested detail_ref/);
  }
  reply = projection;
  assert.deepEqual(await fetchPresentationProjection("http://localhost/extension-projection", surface), projection);
  await assert.rejects(fetchPresentationProjection("http://localhost/extension-projection",
    {...surface, visibility: "owner-only"}), /public-safe/);
  await assert.rejects(fetchPresentationProjection("http://localhost/extension-projection",
    {extension_id: surface.extension_id, extension_revision: surface.extension_revision,
      surface_id: surface.surface_id, surface_kind: surface.surface_kind, title: surface.title,
      view_schema: surface.view_schema, visibility: "public-safe", goal_id: null, generated_at: null,
      review_due_at: null, diagnostic: null, empty_state_title: "Empty", empty_state_detail: "Disabled", state: "empty"}),
    /no published projection/);
} finally {globalThis.fetch = originalFetch;}

console.log("Decision research source-period and published-reference coverage passed");
