/** Aggregate storage has no foreign key or identifier linking it to installations. */
import { validAggregate, validPing } from "../../../loopx/control_plane/runtime/usage_statistics_contract.ts";
import type { Aggregate } from "../../../loopx/control_plane/runtime/usage_statistics_contract.ts";

type Statement = { bind(...args: unknown[]): Statement; all(): Promise<{ results: Record<string, unknown>[] }> };
type Database = { prepare(sql: string): Statement; batch(statements: Statement[]): Promise<unknown> };
export { validAggregate, validPing };
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
