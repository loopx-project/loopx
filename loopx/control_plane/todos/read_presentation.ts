/** Existing Todo list response codecs, after full-source decisions. */
import type {JsonObject} from "../effect_program.ts";
import {stripPythonWhitespace} from "../coordination/todo_agents.ts";
import {INTERNAL_STATE_SHAPE_PATTERNS} from "../goals/vision_checkpoint.ts";
import {compactTodoReadText} from "./read_codec.ts";
import codec from "./read_legacy_codec.json" with {type: "json"};

const layout = codec.list_constants;
const dict = (v: unknown): v is JsonObject => v !== null && typeof v === "object" && !Array.isArray(v);
const nonempty = (v: unknown): boolean => Array.isArray(v) ? v.length > 0 : dict(v) ? Object.keys(v).length > 0 : Boolean(v);
function compactText(v: unknown): unknown {
  if (typeof v !== "string") return v;
  const full = stripPythonWhitespace(v), points = Array.from(full);
  return points.length <= 180 ? full : stripPythonWhitespace(points.slice(0, 177).join("")) + "...";
}
function compactItem(value: unknown, thin = false): unknown {
  if (!dict(value)) return value;
  const fields = thin ? layout.THIN_TODO_LIST_ITEM_FIELDS : layout._ITEM_FIELDS;
  const result: JsonObject = {};
  for (const key of fields) if (value[key] != null) result[key] = ["text", ...(thin ? [] : ["title", "note"])].includes(key) ? compactText(value[key]) : value[key];
  if (!thin && (value.task_class === "blocker" || value.status === "blocked")) {
    const reason = compactTodoReadText(String(value.reason || ""), 220);
    if (reason && !INTERNAL_STATE_SHAPE_PATTERNS.some(rule => rule.test(reason))) result.reason = reason;
  }
  return thin ? Object.fromEntries(Object.entries(result).map(([key, v]) => [key, compactThinValue(v)])) : result;
}
function compactThinValue(value: unknown): unknown {
  if (typeof value === "string") return compactText(value);
  if (Array.isArray(value)) return value.slice(0, 3).map(compactThinValue);
  if (dict(value)) return Object.fromEntries(Object.entries(value).slice(0, 8).map(([k, v]) => [k, compactThinValue(v)]));
  return value;
}

export function compactTodoReadSummary(summary: JsonObject, role: string, limit: number | null): JsonObject {
  const result: JsonObject = {}, lanes: JsonObject = {};
  let omittedLanes = 0, omittedDicts = 0;
  for (const [key, value] of Object.entries(summary)) {
    if (Array.isArray(value)) {
      const cap = limit ?? (layout._RETAINED_LANE_LIMITS as Record<string, number>)[key];
      if (cap === undefined) {omittedLanes += Number(nonempty(value)); continue;}
      const retained = limit === null && key === "items" ? value.filter(row => !dict(row) || row.status !== "done") : value;
      result[key] = retained.slice(0, cap).map(row => compactItem(row));
      if (retained.length > cap) lanes[key] = {shown: cap, total: retained.length};
    } else if (dict(value)) {
      if (layout._RETAINED_DICTS.includes(key)) result[key] = value;
      else omittedDicts += Number(nonempty(value));
    } else result[key] = value;
  }
  result.payload_compaction = limit === null ? {
    schema_version: layout.AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION, role, item_limit: 12, item_text_limit: 180,
    terminal_items_in_hot_path: false, compacted_lanes: lanes, omitted_nonempty_lane_count: omittedLanes,
    omitted_nonempty_dict_count: omittedDicts,
    full_detail_cold_path: "todo list with --role, --status, or --todo-id; todo list without --agent-id; or active state",
  } : {
    schema_version: layout.EXPLICIT_LIMIT_TODO_LIST_SUMMARY_SCHEMA_VERSION, view: "explicit_limit_cold_path", role,
    item_limit: limit, item_text_limit: 180, compacted_lanes: lanes, omitted_nonempty_dict_count: omittedDicts,
    full_detail_cold_path: "todo list without --limit or active state",
  };
  return result;
}

export function todoReadProjectionContract(matched: number, returned: number, limit: number | null): JsonObject {
  return {schema_version: layout.AGENT_LANE_TODO_LIST_PROJECTION_SCHEMA_VERSION,
    view: limit === null ? "agent_lane_hot_path" : "explicit_limit_cold_path", matched_todo_count: matched,
    returned_todo_count: returned, item_limit_per_role: limit ?? 12, counts_cover_full_match: true,
    full_detail_cold_paths: limit === null ? ["todo list with --role, --status, or --todo-id", "todo list without --agent-id", "active state"] :
      ["todo list without --limit", "active state"]};
}

export function compactThinTodoRead(payload: JsonObject): JsonObject {
  const result = {...payload};
  for (const key of ["state_file", "project", "filter_semantics"]) delete result[key];
  const limit = Math.min(2, typeof result.explicit_limit === "number" ? result.explicit_limit : 2);
  const projection = dict(result.todo_list_projection) ? result.todo_list_projection : {};
  const sourceView = String(projection.view || "full_detail"), matched = Number(projection.matched_todo_count || result.todo_count || 0);
  const todos: unknown[] = [];
  for (const role of ["user", "agent"]) {
    const key = `${role}_todos`, summary = result[key];
    if (!dict(summary)) continue;
    const items = Array.isArray(summary.items) ? summary.items.filter(dict) : [];
    const chosen = items.slice(0, limit).map(row => compactItem(row, true));
    todos.push(...chosen);
    result[key] = compactThinTodoReadSummary(summary, {role, items_matched: Number(summary.total_count || items.length),
      items_returned: chosen.length, item_limit_per_role: limit});
  }
  return {...result, todos, matched_todo_count: matched, returned_todo_count: todos.length,
    omitted_todo_count: Math.max(0, matched - todos.length), thin: true,
    todo_list_field_projection: {schema_version: layout.THIN_TODO_LIST_PROJECTION_SCHEMA_VERSION, view: "thin_explicit_view",
      source_view: sourceView, item_container: "todos", matched_todo_count: matched, returned_todo_count: todos.length,
      omitted_todo_count: Math.max(0, matched - todos.length), item_limit_per_role: limit, counts_cover_full_match: true,
      item_fields: layout.THIN_TODO_LIST_ITEM_FIELDS, item_text_limit: 180, nested_item_limit: 3, nested_dict_field_limit: 8,
      full_detail_cold_paths: ["todo list without --thin", "todo list --todo-id <id> without --thin", "active state"]}};
}

export function compactThinTodoReadSummary(summary: JsonObject, details: {
  role: unknown; items_matched: number; items_returned: number; item_limit_per_role: number;
}): JsonObject {
    const fields: JsonObject = {};
    let omittedLanes = 0, omittedDicts = 0, view = "full_detail";
    for (const [name, value] of Object.entries(summary)) {
      if (Array.isArray(value)) {if (name !== "items") omittedLanes += Number(nonempty(value));}
      else if (dict(value)) {
        if (["work_counts", "deferred_work_counts"].includes(name)) fields[name] = value;
        else if (name === "payload_compaction") view = typeof value.view === "string" && value.view ? value.view :
          value.schema_version === layout.AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION ? "agent_lane_hot_path" : "full_detail";
        else omittedDicts += Number(nonempty(value));
      } else fields[name] = value;
    }
    const total = details.items_matched, omitted = Math.max(0, total - details.items_returned);
    fields.payload_compaction = {schema_version: layout.THIN_TODO_LIST_SUMMARY_SCHEMA_VERSION, view: "thin_explicit_view", source_view: view,
      role: details.role, items_projected_to: "todos", item_limit_per_role: details.item_limit_per_role, item_fields: layout.THIN_TODO_LIST_ITEM_FIELDS, item_text_limit: 180,
      nested_item_limit: 3, items_matched: total, items_returned: details.items_returned, items_omitted: omitted,
      compacted_lanes: omitted ? {items: {shown: details.items_returned, total}} : {}, omitted_nonempty_lane_count: omittedLanes,
      omitted_nonempty_dict_count: omittedDicts, full_detail_cold_path: "todo list without --thin or active state"};
    return fields;
}
