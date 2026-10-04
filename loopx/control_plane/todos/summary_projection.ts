/** Whole-source summary decisions. Python materializes these ordinals as public
 * display records; counts and closure never depend on a presentation budget. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireBoolean, requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {projectTodoSummaryLanes, type TodoSummaryLane} from "./summary_lanes.ts";
import {projectTodoClosure, projectTodoSuccession, SUCCESSION_FACT_COLUMNS, SUCCESSION_EVALUATION_COLUMNS} from "./succession.ts";

type Format = "raw" | "compact" | "active" | "recent" | "gap";
interface DisplayLane {indices: number[]; format: Format}
interface SummaryProjection {
  schema_version: "todo_summary_projection_v0";
  source_indices: number[];
  full_selection: boolean;
  fields: JsonObject;
  lanes: Record<string, DisplayLane>;
  orchestration: {candidate_items: number[]; user_blocker_items: number[]};
}

/** The declared order is this request version's schema, not a hint: the same
 * cells in a different order would silently change meaning. */
export const TODO_SUMMARY_PROJECTION_COLUMNS = [
  "status", "done", "task_class", "has_resume", "resume_ready", "resume_evaluated",
  "acceptance_blocked", "claimed", "preferred", "watch_only", "due_at", "expires_at",
  "sort", "completed_at", "updated_at", "completion_index", "linked_user_action",
  "no_followup", "successor_gap", "handoff_state", "replan", "todo_id", "claim",
  "bound", "blocks", "global", "excluded",
] as const;

/** One whole-source batch carries every Todo, so the co-deployed adapter sends
 * columnar facts. Decoding restores the row objects the lane and closure owners
 * already validate; nothing is defaulted or inferred from absence. */
function decodeRows(request: JsonObject): JsonObject[] {
  const columns = request.columns;
  if (!Array.isArray(columns) || columns.length !== TODO_SUMMARY_PROJECTION_COLUMNS.length ||
      columns.some((name, index) => name !== TODO_SUMMARY_PROJECTION_COLUMNS[index])) {
    throw new EffectRuntimeRequestError("Todo summary row columns do not match the typed adapter order");
  }
  if (!Array.isArray(request.rows)) {
    throw new EffectRuntimeRequestError("Todo summary rows must be a list");
  }
  return request.rows.map((value, ordinal) => {
    if (!Array.isArray(value) || value.length !== columns.length) {
      throw new EffectRuntimeRequestError(`Todo summary row ${ordinal} does not match its declared columns`);
    }
    return Object.fromEntries(TODO_SUMMARY_PROJECTION_COLUMNS.map((name, index) => [name, value[index]]));
  });
}

/** Co-deployed callers can fuse evidence validation with summary projection.
 * This is the same succession owner, not a cache, graph truncation or bypass.
 * Legacy v1 callers retain their existing path; v2 requires the carrier. */
function validateSummarySuccession(request: JsonObject, rows: readonly JsonObject[]): void {
  if (request.schema_version === "todo_summary_projection_request_v1") return;
  const source = requireJsonObject(request.succession, "summary succession");
  if (source.evaluations === undefined) {
    throw new EffectRuntimeRequestError("summary requires full-source succession evaluations");
  }
  const result = projectTodoSuccession(source);
  const evaluations = result.evaluations as unknown[][];
  const facts = source.rows as unknown[][];
  if (evaluations.length !== rows.length) {
    throw new EffectRuntimeRequestError("summary succession cardinality mismatch");
  }
  for (const [index, row] of rows.entries()) {
    const fact = Object.fromEntries(SUCCESSION_FACT_COLUMNS.map((name, column) => [name, facts[index][column]]));
    const evaluation = Object.fromEntries(SUCCESSION_EVALUATION_COLUMNS.map((name, column) => [name, evaluations[index][column]]));
    if (row.todo_id !== fact.todo_id || row.status !== fact.status || row.no_followup !== fact.no_followup ||
        (row.task_class === "advancement_task") !== fact.advancement ||
        row.successor_gap !== evaluation.successor_gap || row.handoff_state !== evaluation.handoff_state) {
      throw new EffectRuntimeRequestError("summary facts disagree with validated succession evidence");
    }
  }
}

/** Allocate a bounded display across claimants, then restore source ordering. */
function claimedVisibility(indices: readonly number[], rows: readonly JsonObject[], limit: number): number[] {
  if (indices.length <= limit) return [...indices];
  const buckets = new Map<string, number[]>();
  for (const index of indices) {
    const claim = rows[index].claim;
    if (typeof claim !== "string" || !claim) continue;
    const bucket = buckets.get(claim) ?? [];
    bucket.push(index); buckets.set(claim, bucket);
  }
  if (!buckets.size) return indices.slice(0, limit);
  const perClaimant = Math.max(1, Math.floor(limit / buckets.size));
  const selected = new Set<number>();
  for (const bucket of buckets.values()) {
    for (const index of bucket.slice(0, perClaimant)) {
      if (selected.size < limit) selected.add(index);
    }
  }
  for (const index of indices) if (selected.size < limit) selected.add(index);
  return indices.filter(index => selected.has(index));
}

export function projectTodoSummary(value: unknown): SummaryProjection {
  const request = requireJsonObject(value, "Todo summary request");
  if (request.schema_version !== "todo_summary_projection_request_v1" &&
      request.schema_version !== "todo_summary_projection_request_v2") {
    throw new EffectRuntimeRequestError("Todo summary request schema mismatch");
  }
  const role = request.role === null ? null : requireStringLiteral(request.role, ["user", "agent"], "role");
  if (request.source_section !== null && typeof request.source_section !== "string") {
    throw new EffectRuntimeRequestError("source_section must be text or null");
  }
  const limit = request.item_limit;
  if (limit !== null && (typeof limit !== "number" || !Number.isSafeInteger(limit) || limit < 0)) {
    throw new EffectRuntimeRequestError("item_limit must be a non-negative integer or null");
  }
  const full = requireBoolean(request.full_selection, "full_selection");
  const rows = decodeRows(request);
  validateSummarySuccession(request, rows);
  // The co-deployed adapter sends source facts, not prose or full Todo bodies.
  for (const row of rows) {
    if ((row.claim !== null && typeof row.claim !== "string") || row.claimed !== Boolean(row.claim)) {
      throw new EffectRuntimeRequestError("summary claimant facts disagree");
    }
    requireBoolean(row.linked_user_action, "linked_user_action");
    if ((row.completed_at !== null && typeof row.completed_at !== "string") ||
        (row.updated_at !== null && typeof row.updated_at !== "string") ||
        typeof row.completion_index !== "number" || !Number.isSafeInteger(row.completion_index)) {
      throw new EffectRuntimeRequestError("invalid summary completion coordinates");
    }
  }
  const hasSelection = request.selection !== null;
  const projected = projectTodoSummaryLanes({
    schema_version: hasSelection ? "todo_summary_lanes_request_v1" : "todo_summary_lanes_request_v0",
    rows, observed_at: request.observed_at, ...(hasSelection ? {selection: request.selection} : {}),
  });
  const source = hasSelection ? projected.source_indices as number[] : rows.map((_, index) => index);
  const fullSelection = full && (!hasSelection || projected.full_selection === true);
  const selected = projected.lanes as Record<TodoSummaryLane, number[]>;
  const fields: JsonObject = {schema_version: "todo_summary_v0", source_section: request.source_section,
    total_count: source.length, work_counts: projected.work_counts,
    open_count: selected.open_items.length, done_count: selected.terminal_items.length,
    advancement_done_count: selected.done_items.filter(index => rows[index].task_class === "advancement_task").length,
    deferred_count: selected.deferred_items.length, monitor_due_count: selected.monitor_due_items.length,
    monitor_schedule_gap_count: selected.monitor_schedule_gap_items.length,
  };
  const lanes: Record<string, DisplayLane> = {};
  const lane = (name: string, indices: readonly number[], cap: number | null = null, format: Format = "compact") => {
    lanes[name] = {indices: cap === null ? [...indices] : indices.slice(0, cap), format};
  };
  lane("first_open_items", selected.projected_open_items, 3);
  lane("first_executable_items", selected.executable_items, 3);
  lane("monitor_open_items", selected.monitor_items);
  lane("monitor_due_items", selected.monitor_due_items, 1);
  lane("monitor_schedule_gap_items", selected.monitor_schedule_gap_items, 1);
  lane("unclaimed_priority_open_items", selected.unclaimed_open_items, 8);
  lane("claimed_open_items", claimedVisibility(selected.claimed_open_items, rows, 16));
  lane("claimed_advancement_open_items", claimedVisibility(selected.claimed_advancement_items, rows, 16));
  lane("claimed_monitor_open_items", claimedVisibility(selected.claimed_monitor_items, rows, 16));
  lane("backlog_items", selected.projected_open_items, 8);
  lane("executable_backlog_items", selected.executable_items, 8);
  lane("deferred_items", selected.projected_deferred_items, 8);
  lane("deferred_resume_candidates", selected.projected_deferred_items.filter(index => rows[index].resume_ready === true), 8);
  lane("items", selected.budgeted_items, limit, "raw");

  // Recent completion is chronological, not ISO-string order or last-edit order.
  // Unknown legacy times remain in totals, but cannot claim a recent timestamp.
  const instant = (value: unknown): bigint | null => typeof value === "string" && value.trim()
    ? parseTodoTimestampMicros(value.trim()) : null;
  const completedAt = rows.map(row => instant(row.completed_at));
  const changedAt = rows.map(row => instant(row.updated_at || row.completed_at));
  const timestamp = (index: number, completionOnly: boolean) => (completionOnly ? completedAt : changedAt)[index];
  const byTime = (completionOnly: boolean) => (left: number, right: number) => {
    const a = timestamp(left, completionOnly), b = timestamp(right, completionOnly);
    if (a !== b) return a === null ? 1 : b === null ? -1 : a > b ? -1 : 1;
    return Number(rows[right].completion_index) - Number(rows[left].completion_index);
  };
  const recent = selected.done_items.filter(index => rows[index].task_class === "advancement_task" && timestamp(index, true) !== null)
    .sort(byTime(true));
  if (recent.length) lane("recent_completed_advancement_items", recent, 16, "recent");
  const gaps = source.filter(index => rows[index].successor_gap === true).sort(byTime(false));
  if (gaps.length) {
    fields.completed_without_successor_count = gaps.length;
    lane("completed_without_successor_items", gaps, 5, "gap");
  }
  if (selected.watch_only_monitor_items.length) {
    fields.watch_only_monitor_count = selected.watch_only_monitor_items.length;
    fields.watch_only_monitor_due_count = selected.watch_only_monitor_due_items.length;
    fields.convergence_open_count = selected.convergent_open_items.length;
  }
  for (const [name, indices, cap] of [
    ["blocker", selected.blocker_items, null], ["resume_blocked", selected.resume_blocked_items, 8],
  ] as const) {
    if (indices.length) {
      fields[name === "blocker" ? "blocker_open_count" : "resume_blocked_count"] = indices.length;
      lane(`${name}_items`, indices, cap);
    }
  }
  if (selected.active_next_action_items.length) lane("active_next_action_items", selected.active_next_action_items, null, "active");
  if (selected.active_next_action_executable_items.length) lane("active_next_action_executable_items", selected.active_next_action_executable_items, null, "active");
  if (selected.claimed_open_items.length) {
    fields.claimed_open_count = selected.claimed_open_items.length;
    fields.unclaimed_open_count = selected.open_items.length - selected.claimed_open_items.length;
    fields.claimed_advancement_open_count = selected.claimed_advancement_items.length;
    fields.claimed_monitor_open_count = selected.claimed_monitor_items.length;
  }
  Object.assign(fields, projectTodoClosure({schema_version: "todo_closure_request_v0", role,
    source_section: request.source_section, full_selection: fullSelection, rows: source.map(index => rows[index])}));
  return {schema_version: "todo_summary_projection_v0", source_indices: source, full_selection: fullSelection, fields, lanes,
    orchestration: {
      candidate_items: role === "agent" ? selected.projected_open_items.filter(index => rows[index].task_class === "advancement_task") : [],
      user_blocker_items: role === "user" ? selected.projected_open_items.filter(index => rows[index].linked_user_action === true) : [],
    }};
}
