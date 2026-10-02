/** Installation-linked daily observations. Never uptime, billing or work authority. */
import { object, validId, MAX_COUNT } from "./usage_statistics_contract.ts";
import { CONTEXTS, DIAGNOSTIC_FEATURES, diagnosticFeature } from "./usage_statistics_diagnostics.ts";
import { MEASUREMENTS } from "./usage_statistics_goal_contract.ts";

export const INSTALLATION_SCHEMA = "loopx_installation_usage_v1";
export const PROFILE_FEATURES = DIAGNOSTIC_FEATURES;
export type ProfileFeature = typeof PROFILE_FEATURES[number];
/** Reuse the diagnostic owner's fixed families; no parallel classification. */
export { diagnosticFeature as profileFeature };
export type InstallationDay = {
  activity_day: string; version: string; context: typeof CONTEXTS[number]; revision: number;
  cli: { feature: ProfileFeature; count: number }[];
  runtime: { measurement: typeof MEASUREMENTS[number]; observed_minutes: number }[];
  truncated: boolean;
};
export type InstallationUsage = { schema: typeof INSTALLATION_SCHEMA; install_id: string; profiles: InstallationDay[] };
function exact(value: Record<string, unknown>, fields: string): boolean { return Object.keys(value).sort().join() === fields; }
export function validInstallationDay(value: unknown): value is InstallationDay {
  if (!object(value) || !exact(value, "activity_day,cli,context,revision,runtime,truncated,version")
    || typeof value.activity_day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.activity_day)
    || !Number.isFinite(Date.parse(value.activity_day)) || new Date(value.activity_day).toISOString().slice(0, 10) !== value.activity_day
    || typeof value.version !== "string" || !/^\d{1,3}\.\d{1,3}\.\d{1,4}$/.test(value.version)
    || !(CONTEXTS as readonly unknown[]).includes(value.context) || typeof value.truncated !== "boolean"
    || !Number.isSafeInteger(value.revision) || Number(value.revision) < 1
    || !Array.isArray(value.cli) || value.cli.length > PROFILE_FEATURES.length
    || !Array.isArray(value.runtime) || value.runtime.length > MEASUREMENTS.length) return false;
  const cli = new Set<unknown>(), runtime = new Set<unknown>();
  return value.cli.every(row => object(row) && exact(row, "count,feature")
    && (PROFILE_FEATURES as readonly unknown[]).includes(row.feature) && !cli.has(row.feature) && !!cli.add(row.feature)
    && Number.isInteger(row.count) && Number(row.count) >= 1 && Number(row.count) <= MAX_COUNT)
    && value.runtime.every(row => object(row) && exact(row, "measurement,observed_minutes")
      && (MEASUREMENTS as readonly unknown[]).includes(row.measurement) && !runtime.has(row.measurement) && !!runtime.add(row.measurement)
      && Number.isInteger(row.observed_minutes) && Number(row.observed_minutes) >= 0 && Number(row.observed_minutes) <= 1440);
}
export function validInstallationUsage(value: unknown): value is InstallationUsage {
  return object(value) && exact(value, "install_id,profiles,schema") && value.schema === INSTALLATION_SCHEMA
    && validId(value.install_id) && Array.isArray(value.profiles) && value.profiles.length >= 1 && value.profiles.length <= 8
    && value.profiles.every(validInstallationDay)
    && new Set(value.profiles.map(row => row.activity_day)).size === value.profiles.length;
}
