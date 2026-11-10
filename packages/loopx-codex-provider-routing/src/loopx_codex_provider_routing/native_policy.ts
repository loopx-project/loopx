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
export type ApiRole = "disabled" | "fallback" | "primary";
export type Policy = { enabled: boolean; slots: string[]; cooldown_seconds: number;
  restart_clients?: boolean; force_quit_clients?: boolean; api_fallback?: boolean;
  steward_api_role?: ApiRole };

/** Billing priority belongs to the consumer, not the model or credential. */
export function apiRole(policy: Policy | undefined, consumer: string): ApiRole {
  if (!policy) return "disabled";
  if (consumer === "worker") return policy.api_fallback === true ? "fallback" : "disabled";
  if (consumer !== "steward") return "disabled";
  const role = policy.steward_api_role;
  if (role === undefined) return policy.api_fallback === true ? "fallback" : "disabled";
  return role === "primary" || role === "fallback" ? role : "disabled";
}

export function modelRoute(input: {consumer: string; native: Record<string, any>; api_ready: boolean}) {
  const native = input.native;
  const role = apiRole(native.policy, input.consumer);
  const primary = input.consumer === "steward" && role === "primary" && input.api_ready;
  const fallback = role === "fallback" && native.policy.enabled === true
    && native.policy.api_fallback === true && native.api_active === true
    && native.login_matches === true && native.recovery_required === false && native.action === "api_active";
  return {role, api: primary || fallback};
}

/** The steward's endpoint pool is separate from the native account ring.
 * Selection observes allowance only; it never changes a login or retries work.
 */
export function stewardRoute(input: {
  now: number; primary: string; pool: string[]; current: string;
  native: Record<string, any>; primary_capacity: Record<string, any> | null;
  blocked_primary?: Record<string, any> | null;
  api_ready?: boolean;
}) {
  const no = {endpoint: null, blocked_primary: input.blocked_primary ?? null};
  if (!Number.isFinite(input.now) || !["antigravity", "codex"].includes(input.current)
      || input.primary !== "antigravity" || !Array.isArray(input.pool)
      || input.pool.length !== 2 || new Set(input.pool).size !== 2
      || !input.pool.includes("antigravity") || !input.pool.includes("codex")) return no;
  const validCapacity = (row: Record<string, any> | null | undefined) => row
    && Number.isFinite(row.observed_at) && typeof row.account_ref === "string" && /^[a-f0-9]{64}$/.test(row.account_ref)
    && typeof row.model_id === "string" && row.model_id.length > 0
    && Number.isFinite(row.remaining_fraction) && row.remaining_fraction >= 0 && row.remaining_fraction <= 1
    && typeof row.reset_time === "string" && Number.isFinite(Date.parse(row.reset_time))
    && typeof row.execution_id === "string" && row.execution_id.length > 0
    && typeof row.quota_exhausted === "boolean" && typeof row.normal_completion === "boolean"
    && row.quota_exhausted !== row.normal_completion;
  if (input.blocked_primary != null && !validCapacity(input.blocked_primary)) return no;
  if (modelRoute({consumer: "steward", native: input.native, api_ready: input.api_ready === true}).api
      && apiRole(input.native.policy, "steward") === "primary") return {endpoint: "codex", blocked_primary: input.blocked_primary ?? null};
  const capacity = input.primary_capacity;
  if (!validCapacity(capacity) || !capacity || input.now < capacity.observed_at
      || input.now - capacity.observed_at > 120) return no;
  let blocked = input.blocked_primary ?? null;
  if (capacity.quota_exhausted && (!blocked || blocked.execution_id !== capacity.execution_id)) blocked = capacity;
  const restored = blocked && (blocked.account_ref !== capacity.account_ref
    || blocked.model_id !== capacity.model_id || blocked.reset_time !== capacity.reset_time
    || input.now >= Date.parse(blocked.reset_time) / 1000);
  if (capacity.remaining_fraction > 0 && (!blocked || restored) && (!capacity.quota_exhausted || restored)) {
    return {endpoint: "antigravity", blocked_primary: blocked};
  }
  const native = input.native;
  // API connection/model-list checks do not prove generation allowance. An
  // already active, explicitly authorized API route remains the native owner's
  // selection; its pre-output key failover retains that owner's own guards.
  const current = Array.isArray(native.accounts)
    ? native.accounts.find((a: any) => a.slot === native.active_slot) : null;
  const fresh = current && Number.isFinite(current.observed_at)
    && current.observed_at <= input.now && input.now - current.observed_at <= 120;
  const usable = native.policy?.enabled === true && native.login_matches === true
    && native.recovery_required === false && (native.api_active === true
      ? modelRoute({consumer: "steward", native, api_ready: input.api_ready === true}).api
      : native.action === "ready" && fresh && current.availability === "available");
  return {endpoint: usable ? "codex" : null, blocked_primary: blocked};
}

/** Diagnostic liveness only; this never grants switching or process authority. */
export type ObserverHealthState = "unavailable" | "stopped" | "unverified" | "upgrade_required"
  | "overdue" | "retrying" | "healthy";
export function observerHealth(input: {
  now: number; owner_locked: boolean | null; build: string;
  heartbeat: { checked_at?: number; deadline_at?: number; next_check_at?: number;
    build?: string; phase?: string } | null;
}) {
  const row = input.heartbeat;
  const result = (state: ObserverHealthState) => ({state,
    checked_at: Number.isFinite(row?.checked_at) ? row!.checked_at : null,
    next_check_at: Number.isFinite(row?.next_check_at) ? row!.next_check_at : null});
  if (input.owner_locked === null) return result("unavailable");
  if (!input.owner_locked) return result("stopped");
  if (!row || !Number.isFinite(row.checked_at) || !Number.isFinite(row.deadline_at)
      || row.checked_at! > input.now + 5 || row.deadline_at! <= row.checked_at!
      || row.deadline_at! > row.checked_at! + 600
      || typeof row.build !== "string"
      || !["checking", "waiting", "retrying"].includes(row.phase ?? "")) return result("unverified");
  if (row.build !== input.build) return result("upgrade_required");
  if (row.deadline_at! < input.now) return result("overdue");
  return result(row.phase === "retrying" ? "retrying" : "healthy");
}

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
  api?: { slot: string; ready: boolean };
}) {
  const accounts = input.observations.map(o => account(o, input.now));
  const onApi = input.api?.slot === input.active;
  const output = (action: string, target: string | null = null, retry_at: number | null = null) =>
    ({ action, target, retry_at, accounts,
      restart_clients: action === "switch" && input.policy.restart_clients === true,
      force_quit_clients: action === "switch" && input.policy.restart_clients === true
        && input.policy.force_quit_clients === true,
      valid_until: action === "switch" && !input.manual_slot
        ? Math.min(...accounts.filter(a => target === input.api?.slot
          ? a.slot === input.active || input.policy.slots.includes(a.slot)
          : a.slot === input.active || a.slot === target).map(a => a.observed_at + 120))
        : null });
  if (input.recovery_required) return output("recovery_required");
  if (!input.login_matches || !input.active) return output("selection_changed");
  if (!input.manual_slot && !input.policy.enabled) return output("disabled");
  const current = accounts.find(a => a.slot === input.active);
  if (!input.manual_slot && !onApi && current?.availability !== "exhausted") {
    return output(current?.availability === "available" ? "ready"
      : current?.availability === "auth_required" ? "auth_required" : "quota_unknown", null, input.now + 60);
  }
  const cooldown = input.last_switch_at == null ? 0 : input.last_switch_at + input.policy.cooldown_seconds;
  if (!input.manual_slot && cooldown > input.now) return output("cooldown", null, cooldown);
  const allowed = input.policy.slots;
  const index = allowed.indexOf(input.active);
  const order = [...allowed.slice(index + 1), ...allowed.slice(0, index + 1)];
  const candidates = order.flatMap(slot => {
    const candidate = accounts.find(a => a.slot === slot && a.availability === "available");
    const used = [candidate?.primary_used, candidate?.secondary_used].filter(number);
    return slot !== input.active && candidate && used.length
      ? [{ slot, remaining: 100 - Math.max(...used) }] : [];
  });
  // The most constrained reported window limits subscription work. Stable sort
  // retains the configured ring for ties; missing observations never rank.
  const target = input.manual_slot
    ? [input.manual_slot].find(slot => slot !== input.active && allowed.includes(slot))
    : candidates.sort((a, b) => b.remaining - a.remaining)[0]?.slot;
  if (target) return output("switch", target);
  if (!input.manual_slot && onApi) {
    if (input.policy.api_fallback !== true) return output("api_disabled_active", null, input.now + 60);
    // No subscription was selectable above. Keep an actually callable API
    // route active, but do not claim availability when every key is cooling or
    // exhausted; the normal all-unavailable wait owns that state.
    if (input.api?.ready === true) return output("api_active", null, input.now + 60);
  }
  // A failed/stale subscription read is not permission to incur API costs.
  // Every authorized subscription must have fresh, measured exhaustion.
  if (!input.manual_slot && input.policy.api_fallback === true && input.api?.ready
      && allowed.length > 0 && allowed.every(slot => accounts.find(a => a.slot === slot)?.availability === "exhausted")) {
    return output("switch", input.api.slot);
  }
  const resets = accounts.filter(a => allowed.includes(a.slot) && a.availability === "exhausted")
    .flatMap(a => a.reset_at && a.reset_at > input.now ? [a.reset_at] : []);
  const uncertain = accounts.some(a => allowed.includes(a.slot) && a.availability === "unknown");
  return output(input.manual_slot ? "target_unavailable" : "all_unavailable", null,
    Math.min(input.now + (uncertain || (onApi && input.policy.api_fallback === true) ? 60 : 300),
      ...(resets.length ? resets : [Infinity])));
}
