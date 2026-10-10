/** Read-only lane selection over one evaluated source, before display limits. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireBoolean, requireJsonObject, requireStringLiteral, optionalNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {gateAddressesAgent, actionAddressesAgent, claimAllowsAgent} from "./agent_scope.ts";
import {authorityUnicodeCompare} from "../coordination/authority_store_codec.ts";

export const TODO_SUMMARY_LANES = [
  "open_items", "terminal_items", "deferred_items", "done_items", "projected_open_items",
  "projected_deferred_items", "budgeted_items", "claimed_open_items", "unclaimed_open_items",
  "executable_items", "blocker_items", "resume_blocked_items", "monitor_items",
  "monitor_due_items", "watch_only_monitor_items", "watch_only_monitor_due_items",
  "non_watch_only_monitor_due_items", "convergent_open_items",
  "monitor_schedule_gap_items", "claimed_advancement_items",
  "claimed_monitor_items", "active_next_action_items", "active_next_action_executable_items",
] as const;
export type TodoSummaryLane = typeof TODO_SUMMARY_LANES[number];
const TASK_CLASSES = ["advancement_task", "continuous_monitor", "user_gate", "user_action", "blocker"] as const;

export interface MonitorSchedule {
  readonly watchOnly: boolean; readonly dueAt: number | null; readonly expiresAt: number | null;
}

interface Row extends MonitorSchedule {
  ordinal: number; status: "open" | "blocked" | "done" | "deferred";
  taskClass: typeof TASK_CLASSES[number]; done: boolean; actionable: boolean;
  claim: boolean; resumeBlocked: boolean; preferred: boolean; sort: readonly [number, number, string, string];
}
function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new EffectRuntimeRequestError(`${label} must be finite`);
  return value;
}
function optionalTime(value: unknown, label: string): number | null {
  return value === null ? null : finite(value, label);
}
export function decodeMonitorSchedule(row: JsonObject): MonitorSchedule {
  return {watchOnly: requireBoolean(row.watch_only, "watch_only"),
    dueAt: optionalTime(row.due_at, "due_at"), expiresAt: optionalTime(row.expires_at, "expires_at")};
}
const monitorIsActive = (row: MonitorSchedule, now: number) => row.expiresAt === null || row.expiresAt > now;
export function monitorIsDue(row: MonitorSchedule, now: number): boolean {
  return monitorIsActive(row, now) && row.dueAt !== null && row.dueAt <= now;
}
export function monitorHasScheduleGap(row: MonitorSchedule, now: number): boolean {
  return monitorIsActive(row, now) && !row.watchOnly && row.dueAt === null;
}
function decodeRow(value: unknown, ordinal: number): Row {
  const row = requireJsonObject(value, `rows[${ordinal}]`);
  const status = requireStringLiteral(row.status, ["open", "blocked", "done", "deferred"], "status");
  const done = requireBoolean(row.done, "done");
  if (done !== (status === "done" || status === "deferred")) throw new EffectRuntimeRequestError("Todo status/done disagree");
  const hasResume = requireBoolean(row.has_resume, "has_resume");
  const ready = row.resume_ready === true;
  if (hasResume && !requireBoolean(row.resume_evaluated, "resume_evaluated")) {
    throw new EffectRuntimeRequestError("Todo display requires a matching full-source resume evaluation");
  }
  const sort = row.sort;
  if (!Array.isArray(sort) || sort.length !== 4 || typeof sort[2] !== "string" || typeof sort[3] !== "string") {
    throw new EffectRuntimeRequestError("Todo presentation sort coordinate is invalid");
  }
  return {ordinal, status, done, taskClass: requireStringLiteral(row.task_class, TASK_CLASSES, "task_class"),
    actionable: status === "open" && (!hasResume || ready) && !requireBoolean(row.acceptance_blocked, "acceptance_blocked"),
    claim: requireBoolean(row.claimed, "claimed"), resumeBlocked: hasResume && row.resume_ready === false,
    preferred: requireBoolean(row.preferred, "preferred"), ...decodeMonitorSchedule(row),
    sort: [finite(sort[0], "priority"), finite(sort[1], "index"), sort[2], sort[3]]};
}
function compare(left: Row, right: Row): number {
  return left.sort[0] - right.sort[0] || left.sort[1] - right.sort[1] ||
    authorityUnicodeCompare(left.sort[2], right.sort[2]) || authorityUnicodeCompare(left.sort[3], right.sort[3]);
}
export interface WorkCountRow {readonly actionable: boolean; readonly taskClass: string}

/** Counts describe the observed source, never the size of a display lane.
 * Incomplete legacy sources yield lower bounds; unseen work is not classified. */
export function countTodoWork(rows: readonly WorkCountRow[], sourceOpenCount: number,
  complete: boolean, agentId: string | null = null): JsonObject {
  if (!Number.isSafeInteger(sourceOpenCount) || sourceOpenCount < rows.length) {
    throw new EffectRuntimeRequestError("source open count cannot be smaller than observed work");
  }
  return {schema_version: "todo_work_counts_v0", open: sourceOpenCount,
    advancement: rows.filter(row => row.actionable && row.taskClass === "advancement_task").length,
    monitor: rows.filter(row => row.actionable && row.taskClass === "continuous_monitor").length,
    hidden: sourceOpenCount - rows.length,
    complete: complete && sourceOpenCount === rows.length, agent_id: agentId};
}

/** Filter already evaluated full-source rows. Keep their original ordinals:
 * consumers must not confuse a selected position with a source identity. */
function selectRows(rows: readonly Row[], source: readonly unknown[], value: unknown): {rows: Row[]; full: boolean} {
  const selection = requireJsonObject(value, "Todo read selection");
  const role = requireStringLiteral(selection.role, ["user", "agent"], "selection role");
  const status = selection.status == null ? null :
    requireStringLiteral(selection.status, ["open", "blocked", "done", "deferred"], "selection status");
  const todo = optionalNonEmptyString(selection.todo_id, "selection todo_id");
  const agent = optionalNonEmptyString(selection.agent_id, "selection agent_id");
  const addressed = rows.filter(row => {
    const raw = requireJsonObject(source[row.ordinal], "Todo source row");
    const optional = (key: string) => optionalNonEmptyString(raw[key], key);
    const id = optional("todo_id");
    const scope = {claim: optional("claim"), bound: optional("bound"), blocks: optional("blocks"),
      global: requireBoolean(raw.global, "global"), excluded: requireStringArray(raw.excluded, "excluded")};
    const visible = !agent || (role === "agent" ? claimAllowsAgent(scope, agent) :
      row.taskClass === "user_gate" ? gateAddressesAgent(scope, agent) : actionAddressesAgent(scope, agent));
    return (!status || row.status === status) && (!todo || id === todo) && visible;
  });
  return {rows: addressed, full: !status && !todo && !agent};
}

export function projectTodoSummaryLanes(value: unknown): JsonObject {
  const request = requireJsonObject(value, "Todo summary lane request");
  if (!["todo_summary_lanes_request_v0", "todo_summary_lanes_request_v1"].includes(String(request.schema_version)) || !Array.isArray(request.rows)) {
    throw new EffectRuntimeRequestError("Todo summary lane request schema mismatch");
  }
  const decoded = request.rows.map(decodeRow), now = finite(request.observed_at, "observed_at");
  const selection = request.schema_version === "todo_summary_lanes_request_v1"
    ? selectRows(decoded, request.rows, request.selection) : {rows: decoded, full: true};
  const rows = selection.rows;
  const open = rows.filter(row => !row.done), terminal = rows.filter(row => row.done);
  const deferred = terminal.filter(row => row.status === "deferred"), done = terminal.filter(row => row.status !== "deferred");
  const ordered = [...open].sort(compare), orderedDeferred = [...deferred].sort(compare);
  const claimed = ordered.filter(row => row.claim);
  const executable = ordered.filter(row => row.actionable && row.taskClass === "advancement_task");
  const monitors = ordered.filter(row => row.actionable && row.taskClass === "continuous_monitor");
  const due = monitors.filter(row => monitorIsDue(row, now));
  const watchOnlyMonitors = monitors.filter(row => row.watchOnly);
  const watchOnlyDue = due.filter(row => row.watchOnly);
  const nonWatchOnlyDue = due.filter(row => !row.watchOnly);
  const missing = monitors.filter(row => monitorHasScheduleGap(row, now));
  const selected = {
    open_items: open, terminal_items: terminal, deferred_items: deferred, done_items: done,
    projected_open_items: ordered, projected_deferred_items: orderedDeferred,
    budgeted_items: [...ordered, ...orderedDeferred, ...done], claimed_open_items: claimed,
    unclaimed_open_items: ordered.filter(row => !row.claim), executable_items: executable,
    blocker_items: ordered.filter(row => row.status === "blocked" && row.taskClass === "blocker"),
    resume_blocked_items: ordered.filter(row => row.resumeBlocked), monitor_items: monitors,
    monitor_due_items: due, watch_only_monitor_items: watchOnlyMonitors,
    watch_only_monitor_due_items: watchOnlyDue,
    non_watch_only_monitor_due_items: nonWatchOnlyDue,
    convergent_open_items: open.filter(row => !(row.taskClass === "continuous_monitor" && row.watchOnly)),
    monitor_schedule_gap_items: missing,
    claimed_advancement_items: executable.filter(row => row.claim), claimed_monitor_items: monitors.filter(row => row.claim),
    active_next_action_items: ordered.filter(row => row.preferred),
    active_next_action_executable_items: executable.filter(row => row.preferred),
  } satisfies Record<TodoSummaryLane, readonly Row[]>;
  const lanes = Object.fromEntries(TODO_SUMMARY_LANES.map(key => [key, selected[key].map(row => row.ordinal)]));
  return {schema_version: "todo_summary_lanes_v0", lanes,
    ...(request.schema_version === "todo_summary_lanes_request_v1" ? {
      source_indices: rows.map(row => row.ordinal), full_selection: selection.full} : {}),
    work_counts: countTodoWork(open, open.length, true)};
}

/** Compatibility summaries may contain only display fragments. Preserve their
 * declared open total while classifying only unique observed rows. */
export function projectLegacyTodoWorkCounts(value: unknown): JsonObject {
  const request = requireJsonObject(value, "legacy Todo work counts");
  if (request.schema_version !== "todo_work_counts_request_v0" || !Array.isArray(request.rows)) {
    throw new EffectRuntimeRequestError("Todo work count request schema mismatch");
  }
  const observed = new Map<string, WorkCountRow>();
  let consistent = true;
  for (const value of request.rows) {
    const row = requireJsonObject(value, "work count row");
    if (typeof row.identity !== "string" || !row.identity) throw new EffectRuntimeRequestError("work count identity is required");
    const current = {
      actionable: requireBoolean(row.actionable, "actionable"),
      taskClass: requireStringLiteral(row.task_class, TASK_CLASSES, "task_class"),
    };
    const previous = observed.get(row.identity);
    if (previous && (previous.actionable !== current.actionable || previous.taskClass !== current.taskClass)) consistent = false;
    if (!previous) observed.set(row.identity, current);
  }
  const declared = request.source_open_count;
  const known = typeof declared === "number" && Number.isSafeInteger(declared) && declared >= observed.size;
  return countTodoWork([...observed.values()], known ? declared : observed.size,
    known && consistent && declared === observed.size, typeof request.agent_id === "string" ? request.agent_id : null);
}
