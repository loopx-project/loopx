import { jsonObject } from "../runtime_decode.ts";

/** A typed blocked Turn may close without spend only with a bounded retry. */
export function isBoundedBlockedRetry(value: unknown, todoId: string | null): boolean {
  const retry = jsonObject(value);
  if (!retry || retry.schema_version !== "quota_blocked_retry_v0" ||
      (retry.source !== "todo" && retry.source !== "turn_settlement") ||
      typeof todoId !== "string" || retry.todo_id !== todoId ||
      typeof retry.observed_at !== "string" || typeof retry.due_at !== "string" ||
      retry.resume_when !== `resume_at:${retry.due_at}`) return false;
  const observed = Date.parse(retry.observed_at);
  const due = Date.parse(retry.due_at);
  const delay = (due - observed) / 1000;
  return Number.isFinite(delay) && delay >= 60 && delay <= 30 * 60;
}
