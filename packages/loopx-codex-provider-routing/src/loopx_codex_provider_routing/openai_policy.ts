/** OpenAI transport outcomes. No credentials, request contents or IO. */
export type KeyRuntime = { id: string; blocked_until?: number; blocked_model?: string | null };
export function apiKeys(input: { now: number; model: string; keys: KeyRuntime[] }) {
  return input.keys.filter(key => !(typeof key.blocked_until === "number"
    && key.blocked_until > input.now && (!key.blocked_model || key.blocked_model === input.model))).map(key => key.id);
}
export function apiOutcome(input: { now: number; status: number; code: string | null; retry_after: number | null; model: string }) {
  const quota = input.status === 429 && ["insufficient_quota", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded"].includes(input.code ?? "");
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
