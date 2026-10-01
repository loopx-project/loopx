import type { TodoItem } from "../../data/status";
import type { WorkspaceAgentTodo, WorkspaceSchedule } from "./personal-workspace-model";

// Readback only: dates and ownership come from the Todo owner. Never calculate
// a next check, expiry, or domain-specific deadline in the presentation layer.
export function monitorTodoReadback(todo: TodoItem): Pick<WorkspaceAgentTodo,
  "cadence" | "nextDueAt" | "expiresAt" | "lastCheckedAt" | "targetKey" | "watchOnly"> {
  return {
    cadence: todo.cadence,
    nextDueAt: todo.next_due_at,
    expiresAt: todo.expires_at,
    lastCheckedAt: todo.last_checked_at,
    targetKey: todo.target_key,
    watchOnly: typeof todo.watch_only === "boolean" ? todo.watch_only
      : todo.watch_only === "true" ? true : todo.watch_only === "false" ? false : null,
  };
}

export function monitorScheduleReadback(todo: WorkspaceAgentTodo): Pick<WorkspaceSchedule,
  "agentId" | "expiresAt" | "nextRunAt" | "previousRunAt" | "schedule" | "stopCondition" | "timezone" | "watchOnly"> {
  return {
    agentId: todo.claimedBy?.trim() || undefined,
    expiresAt: todo.expiresAt || undefined,
    nextRunAt: todo.nextDueAt || undefined,
    previousRunAt: todo.lastCheckedAt || undefined,
    schedule: todo.cadence || undefined,
    stopCondition: todo.resumeWhen || undefined,
    timezone: "UTC",
    watchOnly: todo.watchOnly ?? undefined,
  };
}

export function formatMonitorDate(value: string | undefined, locale: string): string | null {
  // Do not interpret offset-less dates in the browser's local timezone.
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const calendar = /^(\d{4})-(\d{2})-(\d{2})T/.exec(value);
  if (!calendar) return null;
  const [, year, month, day] = calendar;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (date.toISOString().slice(0, 10) !== `${year}-${month}-${day}`) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return `${new Intl.DateTimeFormat(locale, {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    timeZone: "UTC", hourCycle: "h23",
  }).format(timestamp)} UTC`;
}
