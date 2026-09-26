/** Public allowlist shared by the client and Cloudflare collector. No text slots. */
export const PING_SCHEMA = "loopx_usage_ping_v1";
export const AGGREGATE_SCHEMA = "loopx_usage_aggregate_v1";
export const FEATURES = ["status", "quota", "todo", "turn", "project", "connect", "pr-review", "version", "chat", "other"] as const;
export const OUTCOMES = ["ok", "failed", "cancelled"] as const;
export const DURATIONS = ["lt_100ms", "lt_1s", "lt_10s", "lt_60s", "gte_60s"] as const;
export const ERRORS = ["none", "command_failed", "timeout", "connection", "interrupted"] as const;
export const MAX_ROWS = 128;
export const MAX_COUNT = 10000;
export type Feature = typeof FEATURES[number];
export type Outcome = typeof OUTCOMES[number];
export type ErrorKind = typeof ERRORS[number];
export type Counter = { feature: Feature; outcome: Outcome; duration: typeof DURATIONS[number]; error: ErrorKind; count: number };
export type Ping = { schema: typeof PING_SCHEMA; install_id: string; version: string; os: string; arch: string; python: string; channel: string };
export type Aggregate = { schema: typeof AGGREGATE_SCHEMA; counters: Counter[] };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, fields: string[]): boolean {
  return Object.keys(value).sort().join() === fields.sort().join();
}
export function validId(value: unknown): value is string { return typeof value === "string" && UUID.test(value); }
export function validCounter(value: unknown): value is Counter {
  if (!object(value) || !keys(value, ["feature", "outcome", "duration", "error", "count"])) return false;
  if (!(FEATURES as readonly unknown[]).includes(value.feature) || !(OUTCOMES as readonly unknown[]).includes(value.outcome)
    || !(DURATIONS as readonly unknown[]).includes(value.duration) || !(ERRORS as readonly unknown[]).includes(value.error)
    || !Number.isInteger(value.count) || Number(value.count) < 1 || Number(value.count) > MAX_COUNT) return false;
  return value.outcome === "ok" ? value.error === "none"
    : value.outcome === "cancelled" ? value.error === "interrupted"
    : ["command_failed", "timeout", "connection"].includes(String(value.error));
}
export function counterKey(value: Counter): string {
  return [value.feature, value.outcome, value.duration, value.error].join(":");
}
export function validAggregate(value: unknown): value is Aggregate {
  if (!object(value) || !keys(value, ["schema", "counters"]) || value.schema !== AGGREGATE_SCHEMA
    || !Array.isArray(value.counters) || value.counters.length < 1 || value.counters.length > MAX_ROWS) return false;
  return value.counters.every(validCounter) && new Set(value.counters.map(counterKey)).size === value.counters.length;
}
export function validPing(value: unknown): value is Ping {
  return object(value) && keys(value, ["schema", "install_id", "version", "os", "arch", "python", "channel"])
    && value.schema === PING_SCHEMA && validId(value.install_id)
    && typeof value.version === "string" && /^\d{1,3}\.\d{1,3}\.\d{1,4}$/.test(value.version)
    && ["darwin", "linux", "windows", "other"].includes(String(value.os))
    && ["x64", "arm64", "x86", "other"].includes(String(value.arch))
    && typeof value.python === "string" && /^3\.\d{1,2}$/.test(value.python)
    && ["pip", "local_release", "source", "unknown"].includes(String(value.channel));
}
export function durationBucket(ms: number): Counter["duration"] {
  return ms < 100 ? "lt_100ms" : ms < 1000 ? "lt_1s" : ms < 10000 ? "lt_10s" : ms < 60000 ? "lt_60s" : "gte_60s";
}
