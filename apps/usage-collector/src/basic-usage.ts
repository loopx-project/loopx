/** Aggregate storage has no foreign key or identifier linking it to installations. */
import { validAggregate, validPing } from "../../../loopx/control_plane/runtime/usage_statistics_contract.ts";
import type { Aggregate } from "../../../loopx/control_plane/runtime/usage_statistics_contract.ts";
import { validDiagnostics } from "../../../loopx/control_plane/runtime/usage_statistics_diagnostics.ts";
import type { DiagnosticAggregate } from "../../../loopx/control_plane/runtime/usage_statistics_diagnostics.ts";

type Statement = { bind(...args: unknown[]): Statement; all(): Promise<{ results: Record<string, unknown>[] }> };
type Database = { prepare(sql: string): Statement; batch(statements: Statement[]): Promise<unknown> };
export { validAggregate, validPing };
export { validDiagnostics };
export async function recordDiagnostics(db: Database, value: DiagnosticAggregate, receiptDay: string) {
  await db.batch(value.counters.map(row => db.prepare(
    "INSERT INTO diagnostic_counts (receipt_day, activity_day, version, context, feature, operation, outcome, error, duration, signal, count) " +
    "VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11) " +
    "ON CONFLICT (receipt_day, activity_day, version, context, feature, operation, outcome, error, duration, signal) DO UPDATE SET count = count + excluded.count",
  ).bind(receiptDay, row.activity_day, row.version, row.context, row.feature, row.operation, row.outcome, row.error, row.duration, row.signal, row.count)));
}
export async function diagnosticStats(db: Database, since: string) {
  const totals: Record<string, Record<string, number>> = {};
  for (const column of ["feature", "operation", "outcome", "error", "duration", "version", "context", "signal"]) {
    const rows = await db.prepare(`SELECT ${column} AS key, SUM(count) AS n FROM diagnostic_counts WHERE receipt_day >= ?1 GROUP BY ${column}`).bind(since).all();
    totals[column] = {};
    for (const row of rows.results) if (Number(row.n) >= 5) totals[column][String(row.key)] = Number(row.n);
  }
  return { schema: "loopx_usage_diagnostic_stats_v1", definition: "Independent marginal totals from versioned, lossy observations in the last 30 receipt days. Signals describe observed transitions, not unique Goals, people, independent outcome quality or billing. Context is voluntary self-report, never inferred. Cells below 5 omitted.", totals };
}
export async function recordAggregate(db: Database, value: Aggregate, day: string) {
  await db.batch(value.counters.map(row => db.prepare(
    "INSERT INTO usage_counts (day, feature, outcome, duration, error, count) VALUES (?1, ?2, ?3, ?4, ?5, ?6) " +
    "ON CONFLICT (day, feature, outcome, duration, error) DO UPDATE SET count = count + excluded.count",
  ).bind(day, row.feature, row.outcome, row.duration, row.error, row.count)));
}
export async function aggregateStats(db: Database, since: string) {
  // Only publish independent marginal totals, not high-dimensional combinations.
  const totals: Record<string, Record<string, number>> = {};
  for (const column of ["feature", "outcome", "duration", "error"]) {
    const rows = await db.prepare(`SELECT ${column} AS key, SUM(count) AS n FROM usage_counts WHERE day >= ?1 GROUP BY ${column}`).bind(since).all();
    totals[column] = {};
    for (const row of rows.results) {
      if (Number(row.n) >= 5) totals[column][String(row.key)] = Number(row.n);
    }
  }
  return { schema: "loopx_usage_aggregate_stats_v1", definition: "Lossy CLI invocation counts received in the last 30 UTC days; not people, installations or accepted Goal outcomes. Cells below 5 omitted.", totals };
}

export { validGoalAggregate } from "../../../loopx/control_plane/runtime/usage_statistics_goal_contract.ts";
import { MEASUREMENTS } from "../../../loopx/control_plane/runtime/usage_statistics_goal_contract.ts";
import type { GoalAggregate } from "../../../loopx/control_plane/runtime/usage_statistics_goal_contract.ts";
export async function recordGoals(db: Database, value: GoalAggregate, day: string) {
  await db.batch(value.counters.map(row => db.prepare(
    "INSERT INTO goal_duration_counts (day, measurement, host, span, duration, count) VALUES (?1, ?2, ?3, ?4, ?5, ?6) " +
    "ON CONFLICT (day, measurement, host, span, duration) DO UPDATE SET count = count + excluded.count",
  ).bind(day, row.measurement, row.host, row.span, row.duration, row.count)));
}
export async function goalStats(db: Database, since: string) {
  // Each measurement is an independent population. Never add the three clocks.
  const measurements: Record<string, Record<string, Record<string, number>>> = {};
  for (const measurement of MEASUREMENTS) {
    const totals: Record<string, Record<string, number>> = {};
    for (const column of ["span", "duration", "host"]) {
      const rows = await db.prepare(`SELECT ${column} AS key, SUM(count) AS n FROM goal_duration_counts WHERE day >= ?1 AND measurement = ?2 GROUP BY ${column}`).bind(since, measurement).all();
      totals[column] = {};
      for (const row of rows.results) if (Number(row.n) >= 5) totals[column][String(row.key)] = Number(row.n);
    }
    measurements[measurement] = totals;
  }
  return { schema: "loopx_goal_usage_stats_v1", definition: "Lossy observed Goal-day samples in the last 30 receipt days, grouped by measurement. quota_cycle is admitted quota-to-successful-spend elapsed time for any Host, including pauses; codex_turn uses bound session timing; host_call uses direct invocation checkpoints. These populations overlap: do not add counts or durations. Span is first-to-last observed interval. Not unique Goals, completion, CPU time or billing. Cells below 5 omitted.", measurements };
}
