// @ts-expect-error The smoke compiler intentionally runs without @types/node.
import { readFileSync } from "node:fs";
import {
  formatCostUsd,
  formatTokenCount,
  formatUsageValue,
  goalUsageLabel,
  hasGoalUsage,
} from "../src/features/personal-workspace/personal-workspace-model.js";

function assert(condition: boolean, message: string) {
  if (!condition) {
    throw new Error(message);
  }
}

function includes(source: string, snippet: string, label: string) {
  assert(source.includes(snippet), `missing ${label}: ${snippet}`);
}

function excludes(source: string, snippet: string, label: string) {
  assert(!source.includes(snippet), `unexpected ${label}: ${snippet}`);
}

const statusSource = readFileSync("src/data/status.ts", "utf8");
const dashboardSource = readFileSync("src/views/dashboard-page.tsx", "utf8");
const workspaceI18nSource = readFileSync("src/features/personal-workspace/i18n.tsx", "utf8");
const packageSource = readFileSync("package.json", "utf8");
const exampleStatus = readFileSync("../../../examples/status.example.json", "utf8");
const promotionGateWarningFixture = readFileSync("../../../examples/dashboard-promotion-gate-warning-status.json", "utf8");
const decisionFreshnessFixture = readFileSync("../../../examples/dashboard-home-browser-smoke.mjs", "utf8");
const contractSource = readFileSync("../../../docs/status-data-contract.md", "utf8");
const promotionGateWarningStatus = JSON.parse(promotionGateWarningFixture);

for (const [field, label] of [
  ["progress_signal_run_count_24h", "24h progress signal field"],
  ["progress_signal_run_count_7d", "7d progress signal field"],
] as const) {
  includes(statusSource, `${field}: z.number().optional().default(0)`, `schema ${label}`);
  includes(statusSource, `${field}: 0`, `default totals ${label}`);
  includes(exampleStatus, `"${field}"`, `example ${label}`);
  includes(contractSource, field, `contract ${label}`);
}

for (const [field, label] of [
  ["status_contract", "status contract payload field"],
  ["schema_version", "status contract schema version"],
  ["reload_hint", "status contract daemon reload hint"],
  ["event_ledger_summary", "event ledger summary payload field"],
  ["by_class_24h", "24h event class counts"],
  ["by_class_7d", "7d event class counts"],
  ["latest_event_class", "latest event class"],
] as const) {
  includes(statusSource, field, `schema ${label}`);
  includes(exampleStatus, `"${field}"`, `example ${label}`);
  includes(contractSource, field, `contract ${label}`);
}

for (const [field, label] of [
  ["decision_freshness_summary", "decision freshness payload field"],
  ["requires_decision_point_rebase", "decision point rebase guard"],
  ["newer_event_count_7d", "newer event count"],
] as const) {
  includes(statusSource, field, `schema ${label}`);
  if (field === "decision_freshness_summary") {
    includes(exampleStatus, `"${field}"`, `example ${label}`);
  } else {
    includes(decisionFreshnessFixture, field, `decision freshness fixture ${label}`);
  }
  includes(contractSource, field, `contract ${label}`);
}

for (const [field, label] of [
  ["promotion_readiness_summary", "promotion readiness payload field"],
  ["promotion_gate", "promotion gate payload field"],
  ["can_promote", "promotion gate promote decision"],
  ["should_warn", "promotion gate warning decision"],
  ["requires_readiness_run", "promotion readiness rerun guard"],
  ["freshness_window_hours", "promotion readiness freshness window"],
] as const) {
  includes(statusSource, field, `schema ${label}`);
  includes(exampleStatus, `"${field}"`, `example ${label}`);
  includes(contractSource, field, `contract ${label}`);
}

assert(
  promotionGateWarningStatus.promotion_gate.gate_state === "warning",
  "promotion gate warning fixture gate_state",
);
assert(
  promotionGateWarningStatus.promotion_gate.can_promote === false,
  "promotion gate warning fixture can_promote",
);
assert(
  promotionGateWarningStatus.promotion_gate.should_warn === true,
  "promotion gate warning fixture should_warn",
);
assert(
  promotionGateWarningStatus.promotion_gate.readiness.freshness_status === "missing",
  "promotion gate warning fixture freshness",
);
includes(
  promotionGateWarningFixture,
  "python3 examples/canary/canary-promotion-readiness-smoke.py",
  "promotion gate warning fixture recommended action",
);

includes(
  packageSource,
  "personal-workspace-browser-smoke.mjs",
  "canonical personal workspace browser smoke script",
);
excludes(
  packageSource,
  "dashboard-promotion-readiness-browser-smoke.mjs",
  "retired legacy Ops promotion readiness browser smoke script",
);

for (const [snippet, label] of [
  ["function buildPersonalHomeModel(", "personal workspace model assembly"],
  ["shareUsageById(payload.usage_summary)", "goal usage projection"],
  ["systemHealth", "system health projection"],
  ["payload.decision_freshness_summary", "decision freshness check"],
] as const) {
  includes(dashboardSource, snippet, label);
}

for (const field of [
  "input_tokens_24h",
  "input_tokens_7d",
  "output_tokens_24h",
  "output_tokens_7d",
  "cost_usd_24h",
  "cost_usd_7d",
  "duration_ms_24h",
  "duration_ms_7d",
] as const) {
  excludes(statusSource, `${field}: z.number().optional().default(0)`, `${field} must preserve an absent measurement`);
}

for (const [snippet, label] of [
  ["tokens24h: sumMeasuredUsage(goalUsage.input_tokens_24h, goalUsage.output_tokens_24h)", "24h token aggregation"],
  ["tokens7d: sumMeasuredUsage(goalUsage.input_tokens_7d, goalUsage.output_tokens_7d)", "7d token aggregation"],
] as const) {
  includes(dashboardSource, snippet, label);
}

assert(hasGoalUsage({ tokens24h: 0 }), "a measured zero must remain visible");
assert(!hasGoalUsage({}), "an entirely absent usage record is unknown");
assert(formatUsageValue(undefined, "Not measured", formatCostUsd) === "Not measured", "missing cost must not display as $0.00");
assert(formatUsageValue(0, "Not measured", formatTokenCount) === "0", "a measured zero token count must display as zero");
assert(
  goalUsageLabel({ costUsd7d: 0 }, { cost: "Cost", duration: "Duration", period24h: "24h", period7d: "7d", tokens: "tokens" })
    === "7d Cost: $0.00",
  "the compact label must distinguish observed zero cost without inventing tokens",
);
assert(
  goalUsageLabel({ durationMs24h: 0 }, { cost: "Cost", duration: "Duration", period24h: "24h", period7d: "7d", tokens: "tokens" })
    === "24h Duration: 0ms",
  "the compact label must use the observed 24h window when 7d is unknown",
);

for (const [snippet, label] of [
  ["drawer.tokens", "localized token label"],
  ["drawer.usageNotMeasured", "localized unknown usage value"],
] as const) {
  includes(workspaceI18nSource, snippet, label);
}

// The packaged Goal workspace scene checks unknown windows and measured zero
// through the rendered reader. Source spelling is not a usage invariant.

console.log("usage-progress smoke ok");
