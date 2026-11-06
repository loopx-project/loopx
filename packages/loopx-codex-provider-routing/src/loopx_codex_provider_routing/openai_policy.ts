/** OpenAI transport outcomes. No credentials, request contents or IO. */
export type KeyRuntime = { id: string; blocked_until?: number; blocked_model?: string | null;
  state?: string; observed_at?: number };
const QUOTA_CODES = ["insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
  "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
  "usage_limit_exceeded"];

/** A streamed rejection updates future availability, never replays this stream. */
export function apiStreamOutcome(value: unknown): {status: number; code: string} | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const event = value as Record<string, unknown>;
  let error: Record<string, unknown>;
  if (event.type === "error") {
    error = event;
  } else if (event.type === "response.failed") {
    const response = event.response as Record<string, unknown> | undefined;
    if (!response || response.status !== "failed" || !response.error ||
        typeof response.error !== "object" || Array.isArray(response.error)) return null;
    error = response.error as Record<string, unknown>;
  } else return null;
  const code = typeof error.code === "string" ? error.code : null;
  if (code !== null && (QUOTA_CODES.includes(code) || code === "rate_limit_exceeded")) {
    return {status: 429, code};
  }
  return null;
}
export function apiKeys(input: { now: number; model: string; keys: KeyRuntime[] }) {
  // Expiry permits a future attempt; it does not establish recovered capacity.
  // Try alternatives before a known rejection, even when requests are spaced
  // farther apart than the cooldown. Oldest failures are reconsidered first.
  const rejectionTime = (key: KeyRuntime) => ["quota_exhausted", "authentication_failed",
    "permission_denied", "rate_limited"].includes(key.state ?? "")
    && typeof key.observed_at === "number" && Number.isFinite(key.observed_at) && key.observed_at >= 0
    && (!key.blocked_model || key.blocked_model === input.model) ? key.observed_at : -1;
  return input.keys.filter(key => !(typeof key.blocked_until === "number"
    && key.blocked_until > input.now && (!key.blocked_model || key.blocked_model === input.model)))
    .sort((a,b) => rejectionTime(a) - rejectionTime(b)).map(key => key.id);
}
export function apiOutcome(input: { now: number; status: number; code: string | null; retry_after: number | null; model: string }) {
  const quota = input.status === 429 && QUOTA_CODES.includes(input.code ?? "");
  const authentication = input.status === 401;
  const permission = input.status === 403;
  const limited = input.status === 429;
  const delay = typeof input.retry_after === "number" && Number.isFinite(input.retry_after)
    ? Math.max(1, input.retry_after) : quota || authentication ? 300 : 30;
  return { state: quota ? "quota_exhausted" : authentication ? "authentication_failed" : permission
    ? "permission_denied" : limited ? "rate_limited" : input.status >= 200 && input.status < 300 ? "accepted" : "failed",
    retry_next_key: quota || authentication || permission || limited,
    blocked_until: quota || authentication || permission || limited ? input.now + delay : 0,
    blocked_model: permission ? input.model : null };
}
