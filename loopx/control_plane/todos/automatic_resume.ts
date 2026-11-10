import type { JsonObject } from "../runtime_decode.ts";
import { jsonObject } from "../runtime_decode.ts";
import { resumeConditionHasKnownPendingTarget } from "./resume_condition.ts";
import { parseTodoTimestampMicros } from "../runtime_timestamp.ts";
import { monitorIsActive } from "./summary_lanes.ts";

export function normalizedTodoId(candidate: unknown): string | null {
  if (typeof candidate !== "string") return null;
  const id = candidate.trim().toLowerCase();
  return /^todo_[a-z0-9_-]{3,64}$/.test(id) ? id : null;
}

function hasScheduledMonitorSource(condition: JsonObject): boolean {
  const time = (value: unknown): number | null => {
    const micros = typeof value === "string" ? parseTodoTimestampMicros(value) : null;
    return micros === null ? null : Number(micros) / 1_000_000;
  };
  const observed = time(condition.evaluated_at), due = time(condition.target_next_due_at);
  const expiry = time(condition.target_expires_at);
  if (condition.clock_provider !== "runtime_clock" || observed === null || due === null ||
      !(condition.target_archive_state === null || condition.target_archive_state === "active") ||
      condition.target_expires_at !== null && expiry === null) return false;
  return monitorIsActive({expiresAt: expiry}, observed) && monitorIsActive({expiresAt: expiry}, due);
}

/** Qualify each current source before its wait is compacted for a consumer. */
export function pendingAutomaticResumeCondition(todo: JsonObject, agentId: string): JsonObject | null {
  const condition = jsonObject(todo.resume_condition);
  if (!normalizedTodoId(todo.todo_id) || !["open", "deferred"].includes(String(todo.status)) ||
      !(todo.claimed_by == null || todo.claimed_by === "" || todo.claimed_by === agentId) ||
      !condition || !["resume_at", "todo_done", "monitor_changed"].includes(String(condition.kind))) return null;
  try {
    return resumeConditionHasKnownPendingTarget(condition, todo) &&
      (condition.kind !== "monitor_changed" || hasScheduledMonitorSource(condition)) ? condition : null;
  } catch {
    return null;
  }
}

function compactResumeCondition(resumeCondition: unknown): JsonObject {
  const condition = typeof resumeCondition === "object" && resumeCondition !== null && !Array.isArray(resumeCondition)
    ? resumeCondition as JsonObject : {};
  const keys = ["schema_version", "resume_when", "satisfied", "kind", "target", "target_todo_id", "target_status",
    "target_archive_state", "target_source_section", "target_task_class", "target_claimed_by", "target_next_due_at",
    "capability", "provider", "scheduled_for", "evaluated_at", "clock_provider", "material_change",
    "material_change_generation", "monitor_change_scope", "scoped_material_change_generation",
    "baseline_generation", "invalid_state", "target_expires_at"];
  const compact: JsonObject = Object.fromEntries(keys.filter(key => key in condition).map(key => [key, condition[key]]));
  if (condition.kind === "resume_at") compact.resume_receipt = condition.resume_receipt ?? null;
  compact.satisfied = false;
  return compact;
}

export function automaticResumeFields(selected: JsonObject): JsonObject {
  const fields: JsonObject = {};
  for (const key of ["resume_when", "resume_monitor_generation"] as const) {
    if (selected[key] !== undefined && selected[key] !== null) fields[key] = selected[key];
  }
  fields.resume_condition = compactResumeCondition(selected.resume_condition);
  return fields;
}

/** Reduce the entire scoped source before display limits hide its first deadline. */
export function selectPendingResume(waitItems: JsonObject[], agentId: string | null) {
  const pending = agentId ? waitItems.map(todo => ({todo,
    condition: pendingAutomaticResumeCondition(todo, agentId)})).filter(
      (source): source is {todo: JsonObject; condition: JsonObject} => source.condition !== null) : [];
  const pendingCount = pending.length;
  let selected: JsonObject | null = null;
  if (pendingCount === waitItems.length && pendingCount > 0) {
    // A dependency needs periodic source rechecks. For a clock-only frontier,
    // use its earliest deadline rather than the first priority-sorted row.
    selected = (pending.find(source => source.condition.kind !== "resume_at") ?? pending.reduce((first, source) => {
      const due = parseTodoTimestampMicros(String(source.condition.scheduled_for));
      const prior = parseTodoTimestampMicros(String(first.condition.scheduled_for));
      return due !== null && prior !== null && due < prior ? source : first;
    })).todo;
  }
  return {count: pendingCount, todo: selected};
}
