/** Native account availability and handoff policy. No credentials or effects here.
 * These states belong to this optional provider, not the LoopX quota ledger.
 */
export type Availability = "available" | "exhausted" | "unknown" | "auth_required";
export type Observation = {
  slot: string;
  observed_at: number;
  status: "ok" | "auth_required" | "unavailable";
  usage?: Record<string, any>;
};
export type Account = {
  slot: string;
  availability: Availability;
  observed_at: number;
  reset_at: number | null;
  primary_used: number | null;
  secondary_used: number | null;
  credits_available: boolean | null;
};
export type Policy = { enabled: boolean; slots: string[]; cooldown_seconds: number; restart_clients?: boolean };

const number = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const percent = (v: unknown): number | null => number(v) && v >= 0 && v <= 100 ? v : null;
const epoch = (v: unknown): number | null => number(v) && v > 0 ? v : null;

export function account(observation: Observation, now: number): Account {
  const result: Account = { slot: observation.slot, availability: "unknown",
    observed_at: observation.observed_at, reset_at: null, primary_used: null,
    secondary_used: null, credits_available: null };
  if (observation.status === "auth_required") return { ...result, availability: "auth_required" };
  if (!number(observation.observed_at) || now < observation.observed_at || now - observation.observed_at > 120) return result;
  if (observation.status !== "ok" || !observation.usage) return result;
  const usage = observation.usage;
  // App Server's multi-bucket view wins. Review/other product buckets do not
  // establish Codex availability. WHAM is the native read-only HTTP fallback.
  const limits = usage.rateLimitsByLimitId != null ? usage.rateLimitsByLimitId.codex
    : usage.rateLimits ?? usage.rate_limit;
  if (!limits || typeof limits !== "object") return result;
  const primary = limits.primary ?? limits.primary_window;
  const secondary = limits.secondary ?? limits.secondary_window;
  const windows = [primary, secondary].filter(v => v != null);
  const values = windows.map(v => ({ used: percent(v.usedPercent ?? v.used_percent),
    reset: epoch(v.resetsAt ?? v.reset_at) }));
  result.primary_used = primary ? percent(primary.usedPercent ?? primary.used_percent) : null;
  result.secondary_used = secondary ? percent(secondary.usedPercent ?? secondary.used_percent) : null;
  const credits = limits.credits ?? usage.credits;
  const balance = typeof credits?.balance === "string" && credits.balance.trim() !== ""
    ? Number(credits.balance) : credits?.balance;
  result.credits_available = credits?.unlimited === true || credits?.hasCredits === true
    || credits?.has_credits === true || (number(balance) && balance > 0) ? true
    : credits && (credits.hasCredits === false || credits.has_credits === false || balance === 0) ? false : null;
  // A reset passing makes an old 100% sample unknown, never a new allowance.
  if (values.some(v => v.used === null || (v.used === 100 && (v.reset === null || v.reset <= now)))) return result;
  const blocked = values.filter(v => v.used === 100);
  if (blocked.length) {
    result.reset_at = Math.max(...blocked.map(v => v.reset!));
    // Subscription exhaustion alone does not prove a credit-funded account is
    // unusable. This observer never buys credits or invokes a paid reset.
    result.availability = result.credits_available === true ? "available"
      : result.credits_available === false ? "exhausted" : "unknown";
  } else if (values.length && limits.allowed !== false && limits.limit_reached !== true) {
    result.availability = "available";
  }
  return result;
}

export function decide(input: {
  now: number; policy: Policy; active: string | null; observations: Observation[];
  last_switch_at: number | null; recovery_required: boolean; login_matches: boolean;
  manual_slot?: string;
}) {
  const accounts = input.observations.map(o => account(o, input.now));
  const output = (action: string, target: string | null = null, retry_at: number | null = null) =>
    ({ action, target, retry_at, accounts,
      restart_clients: action === "switch" && input.policy.restart_clients === true,
      valid_until: action === "switch" && !input.manual_slot
        ? Math.min(...accounts.filter(a => a.slot === input.active || a.slot === target).map(a => a.observed_at + 120))
        : null });
  if (input.recovery_required) return output("recovery_required");
  if (!input.login_matches || !input.active) return output("selection_changed");
  if (!input.manual_slot && !input.policy.enabled) return output("disabled");
  const current = accounts.find(a => a.slot === input.active);
  if (!input.manual_slot && current?.availability !== "exhausted") {
    return output(current?.availability === "available" ? "ready"
      : current?.availability === "auth_required" ? "auth_required" : "quota_unknown", null, input.now + 60);
  }
  const cooldown = input.last_switch_at == null ? 0 : input.last_switch_at + input.policy.cooldown_seconds;
  if (!input.manual_slot && cooldown > input.now) return output("cooldown", null, cooldown);
  const allowed = input.policy.slots;
  const index = allowed.indexOf(input.active);
  const order = [...allowed.slice(index + 1), ...allowed.slice(0, index + 1)];
  const target = (input.manual_slot ? [input.manual_slot] : order).find(slot =>
    slot !== input.active && allowed.includes(slot) && (input.manual_slot
      || accounts.some(a => a.slot === slot && a.availability === "available")));
  if (target) return output("switch", target);
  const resets = accounts.filter(a => allowed.includes(a.slot) && a.availability === "exhausted")
    .flatMap(a => a.reset_at && a.reset_at > input.now ? [a.reset_at] : []);
  const uncertain = accounts.some(a => allowed.includes(a.slot) && a.availability === "unknown");
  return output(input.manual_slot ? "target_unavailable" : "all_unavailable", null,
    Math.min(input.now + (uncertain ? 60 : 300), ...(resets.length ? resets : [Infinity])));
}
