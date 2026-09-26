import { object } from "./usage_statistics_contract.ts";

export const GOAL_SCHEMA = "loopx_goal_usage_aggregate_v1";
export const GOAL_DURATIONS = ["lt_1m", "lt_10m", "lt_1h", "lt_6h", "lt_1d", "lt_7d", "lt_30d", "gte_30d"] as const;
export type GoalDuration = typeof GOAL_DURATIONS[number];
export const MEASUREMENTS = ["host_call", "codex_turn", "quota_cycle"] as const;
export const HOSTS = ["codex_app", "codex_cli", "claude_code", "dsh", "opencode", "trae", "other", "unknown"] as const;
export type Measurement = typeof MEASUREMENTS[number];
export type Host = typeof HOSTS[number];
export function hostCategory(value: unknown): Host {
  if ((HOSTS as readonly unknown[]).includes(value)) return value as Host;
  const aliases: Record<string, Host> = { "codex-app": "codex_app", "codex-app-ssh": "codex_app", codex_app: "codex_app", codex_app_heartbeat: "codex_app", codex_app_ssh_goal: "codex_app", "codex-cli": "codex_cli", "codex-cli-tui": "codex_cli", codex_cli: "codex_cli", codex: "codex_cli", "codex-ide-plugin": "codex_cli", "deepseek-harness-native": "dsh", "claude-code": "claude_code", claude_code: "claude_code", dsh: "dsh", opencode: "opencode", trae_app: "trae", "generic-cli": "other", generic_cli: "other" };
  return typeof value === "string" && Object.hasOwn(aliases, value) ? aliases[value] : "unknown";
}
export type GoalCount = { measurement: Measurement; host: Host; span: GoalDuration; duration: GoalDuration; count: number };
export type GoalAggregate = { schema: typeof GOAL_SCHEMA; counters: GoalCount[] };
export type GoalObservation = { key: string; start: number; end: number; measurement: Measurement; host: Host };
const DAY = 86400000;
export function goalDuration(ms: number): GoalDuration {
  const limits = [60000, 600000, 3600000, 21600000, DAY, 7 * DAY, 30 * DAY];
  return GOAL_DURATIONS[limits.findIndex(limit => ms < limit)] ?? "gte_30d";
}
export function validGoalAggregate(value: unknown): value is GoalAggregate {
  if (!object(value) || Object.keys(value).sort().join() !== "counters,schema" || value.schema !== GOAL_SCHEMA
    || !Array.isArray(value.counters) || !value.counters.length || value.counters.length > 128) return false;
  const keys = new Set<string>();
  return value.counters.every(row => {
    if (!object(row) || Object.keys(row).sort().join() !== "count,duration,host,measurement,span"
      || !(MEASUREMENTS as readonly unknown[]).includes(row.measurement) || !(HOSTS as readonly unknown[]).includes(row.host)
      || !(GOAL_DURATIONS as readonly unknown[]).includes(row.span) || !(GOAL_DURATIONS as readonly unknown[]).includes(row.duration)
      || !Number.isInteger(row.count) || Number(row.count) < 1 || Number(row.count) > 128) return false;
    const key = `${row.measurement}:${row.host}:${row.span}:${row.duration}`;
    if (keys.has(key)) return false;
    keys.add(key); return true;
  });
}
export function validGoalObservation(value: unknown, now: number): value is GoalObservation {
  return object(value) && Object.keys(value).sort().join() === "end,host,key,measurement,start"
    && (MEASUREMENTS as readonly unknown[]).includes(value.measurement) && (HOSTS as readonly unknown[]).includes(value.host)
    && typeof value.key === "string" && /^[a-f0-9]{64}$/.test(value.key)
    && Number.isSafeInteger(value.start) && Number.isSafeInteger(value.end)
    && Number(value.start) > 0 && Number(value.start) <= Number(value.end)
    && Number(value.end) <= now + 1000 && Number(value.end) >= now - 7 * DAY
    && Number(value.end) - Number(value.start) <= (value.measurement === "host_call" ? 120000 : 7 * DAY);
}
