import { validInstallationUsage } from "../../../loopx/control_plane/runtime/usage_statistics_installation_contract.ts";
import type { InstallationUsage } from "../../../loopx/control_plane/runtime/usage_statistics_installation_contract.ts";
export { validInstallationUsage };

type Statement = { bind(...values: unknown[]): Statement; run(): Promise<unknown> };
type Database = { prepare(sql: string): Statement; batch(statements: Statement[]): Promise<unknown> };
/** Full daily snapshots replace only older revisions: transport replay never adds usage. */
export async function recordInstallation(db: Database, payload: InstallationUsage, receiptDay: string) {
  await db.batch(payload.profiles.map(row => db.prepare(
    "INSERT INTO installation_usage (activity_day, install_id, version, context, revision, cli, runtime, truncated, receipt_day) " +
    "VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9) ON CONFLICT(activity_day, install_id) DO UPDATE SET " +
    "revision=excluded.revision, cli=excluded.cli, runtime=excluded.runtime, truncated=excluded.truncated, receipt_day=excluded.receipt_day " +
    "WHERE excluded.revision > installation_usage.revision AND excluded.context = installation_usage.context AND excluded.version = installation_usage.version",
  ).bind(row.activity_day, payload.install_id, row.version, row.context, row.revision, JSON.stringify(row.cli),
    JSON.stringify(row.runtime), Number(row.truncated), receiptDay)));
}
