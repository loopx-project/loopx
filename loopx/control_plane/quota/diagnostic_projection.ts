/** Presentation of known Todo diagnostic lanes, after the decision is made.
 * No selection, authority, task-text shortening or suffix-based field removal.
 * Native callers provide the original capture reader; legacy callers retain
 * their explicit current-state detail command, which cannot recover an older
 * decision. This projection grants no admission or execution authority.
 */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject} from "../runtime_decode.ts";
import {TODO_CANONICAL_READ_RECORD_FIELDS} from "../coordination/coordination_state_contract.ts";

const AGENT_LIMITS: Readonly<Record<string, number>> = {
  first_executable_items: 3, unclaimed_priority_open_items: 3,
  monitor_due_items: 1, monitor_capability_blocked_due_items: 2,
  monitor_schedule_gap_items: 1, current_agent_blocker_items: 2,
};
const USER_LIMITS: Readonly<Record<string, number>> = {
  first_open_items: 3, gate_open_items: 3, active_next_action_items: 3,
};
// These are registered summary diagnostics, not a pattern over future lists.
const DIAGNOSTIC_LANES = new Set([
  "items", "first_open_items", "first_executable_items", "gate_open_items",
  "monitor_open_items", "monitor_due_items", "watch_only_monitor_due_items",
  "non_watch_only_monitor_due_items", "monitor_capability_blocked_due_items",
  "monitor_schedule_gap_items", "active_next_action_items", "active_next_action_executable_items",
  "backlog_items", "executable_backlog_items", "unclaimed_priority_open_items",
  "claimed_open_items", "claimed_advancement_open_items", "claimed_monitor_open_items",
  "current_agent_claimed_open_items", "current_agent_claimed_advancement_items",
  "current_agent_claimed_monitor_items", "claimed_by_others_items", "current_agent_blocker_items",
  "deferred_items", "deferred_resume_candidates", "current_agent_deferred_resume_candidates",
  "unclaimed_deferred_resume_candidates", "other_agent_deferred_resume_candidates",
  "executor_excluded_self_deferred_resume_candidates", "resume_blocked_items", "blocker_items",
  "recent_completed_advancement_items", "completed_without_successor_items",
  "other_agent_bound_user_action_items",
]);
const ITEM_FIELDS = new Set<string>([...TODO_CANONICAL_READ_RECORD_FIELDS, "index"]);
function knownItems(items: unknown[]): boolean {
  const scalar = (value: unknown) => value === null || ["string", "number", "boolean"].includes(typeof value);
  return items.every(value => {
    const item = jsonObject(value);
    return item !== null && (item.schema_version === undefined || item.schema_version === "todo_item_v0") &&
      Object.entries(item).every(([key, field]) => ITEM_FIELDS.has(key) &&
        (scalar(field) || (Array.isArray(field) && field.every(scalar))));
  });
}

function compactSummary(summary: JsonObject, role: "agent" | "user", detail: string, captured: boolean): JsonObject {
  if (summary.schema_version !== undefined && summary.schema_version !== "todo_summary_v0") return summary;
  const limits = role === "agent" ? AGENT_LIMITS : USER_LIMITS;
  const schema = role === "agent" ? "quota_cli_todo_summary_compaction_v0" : "quota_cli_user_todo_summary_compaction_v0";
  const compact = {...summary}, previous = jsonObject(summary.payload_compaction);
  const owned = previous?.schema_version === schema;
  const omitted: JsonObject = {...(owned ? jsonObject(previous?.omitted_lanes) : {})};
  for (const [key, value] of Object.entries(summary)) {
    if (!DIAGNOSTIC_LANES.has(key) || !Array.isArray(value) || !knownItems(value)) continue;
    const limit = limits[key] ?? 0;
    if (limit) compact[key] = value.slice(0, limit); // Complete items, including unknown fields.
    else delete compact[key];
    if (value.length > limit) omitted[key] = value.length - limit;
  }
  for (const [parentKey, fields] of [
    ["claim_scope", ["other_agent_claimed_items", "blocked_claimed_items"]],
    ["todo_succession_warning", ["items"]],
  ] as const) {
    const parent = jsonObject(summary[parentKey]);
    if (!parent) continue;
    const projected = {...parent};
    for (const key of fields) {
      const value = parent[key];
      if (!Array.isArray(value) || !knownItems(value)) continue;
      if (parentKey === "todo_succession_warning" && !Object.hasOwn(parent, "todo_ids")) {
        const ids = value.map(item => jsonObject(item)?.todo_id).filter(id => typeof id === "string" && id).slice(0, 3);
        if (ids.length) projected.todo_ids = ids;
      }
      delete projected[key];
      if (value.length) omitted[`${parentKey}.${key}`] = value.length;
    }
    compact[parentKey] = projected;
  }
  const metadata: JsonObject = {...(owned ? previous : previous ? {source_projection: previous} : {}),
    schema_version: schema,
    retained_item_lanes: Object.keys(limits).filter(key => key !== "current_agent_blocker_items" ||
      (Array.isArray(summary[key]) && (summary[key] as unknown[]).length > 0)).sort(),
    omitted_lanes: omitted};
  delete metadata.full_detail_cold_path;
  delete metadata.full_detail_capture_ref;
  metadata[captured ? "full_detail_capture_ref" : "full_detail_cold_path"] = detail;
  compact.payload_compaction = metadata;
  return compact;
}

/** A coarse operation for all current callers, never a bridge call per row. */
export function projectQuotaDiagnostics(payload: JsonObject, options: JsonObject = {}): JsonObject {
  const agentDetail = options.agent_detail === true, userDetail = options.user_detail === true;
  const captured = options.capture_ref === "$.source_capture";
  const command = (role: "agent" | "user", mode: string) =>
    captured ? "$.source_capture" : `quota ${mode} --include-detail ${role}-todos`;
  if ((payload.mode === "status" || payload.mode === "plan") && jsonObject(payload.groups)) {
    const row = (value: unknown): unknown => {
      const source = jsonObject(value);
      if (!source) return value;
      const result = {...source};
      for (const role of ["agent", "user"] as const) {
        const key = `${role}_todos`, summary = jsonObject(source[key]);
        if (summary && !(role === "agent" ? agentDetail : userDetail))
          result[key] = compactSummary(summary, role, command(role, String(payload.mode)), captured);
      }
      return result;
    };
    return {...payload, groups: Object.fromEntries(Object.entries(payload.groups as JsonObject)
      .map(([state, rows]) => [state, Array.isArray(rows) ? rows.map(row) : rows])),
      ...(Object.hasOwn(payload, "next_automatic_turn") ? {next_automatic_turn: row(payload.next_automatic_turn)} : {})};
  }
  const result = {...payload}, roles: string[] = [];
  for (const role of ["agent", "user"] as const) {
    const key = `${role}_todo_summary`, summary = jsonObject(payload[key]);
    if (!summary || (role === "agent" ? agentDetail : userDetail)) continue;
    result[key] = compactSummary(summary, role, command(role, "should-run"), captured);
    if (result[key] !== summary) roles.push(role);
  }
  if (roles.length) result.todo_summary_projection = {
    schema_version: "quota_cli_todo_summary_compaction_v0", mode: "compact_hot_path",
    compacted_roles: roles, detail_ref: command(roles[0] === "agent" ? "agent" : "user", "should-run"),
  };
  // Candidate collections and portfolio text are decision context, not mere
  // duplicate counters. Preserve them rather than relying on a fresh guard
  // or a truncated summary to recover a different candidate's requirements.
  return result;
}
