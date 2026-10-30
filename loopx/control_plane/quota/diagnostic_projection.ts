/** Presentation of known Todo diagnostic lanes, after the decision is made.
 * No selection, authority, task-text shortening or suffix-based field removal.
 * Native callers provide the original capture reader; legacy callers retain
 * their explicit current-state detail command, which cannot recover an older
 * decision. This projection grants no admission or execution authority.
 */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, sourceNumberTokens} from "../runtime_decode.ts";
import {TODO_CANONICAL_READ_RECORD_FIELDS} from "../coordination/coordination_state_contract.ts";
import {canonicalAuthorityJson, authorityUnicodeCompare} from "../coordination/authority_store_codec.ts";

type ProjectionPath = (string | number)[];
type ProjectionChange = {path: ProjectionPath; value: unknown} |
  {path: ProjectionPath; from: ProjectionPath} | {path: ProjectionPath; remove: true};

function contextObjectPaths(source: JsonObject): WeakMap<object, ProjectionPath> {
  const paths = new WeakMap<object, ProjectionPath>();
  const index = (value: unknown, path: ProjectionPath): void => {
    if (value === null || typeof value !== "object" || paths.has(value)) return;
    paths.set(value, path);
    if (Array.isArray(value)) value.forEach((item, i) => index(item, [...path, i]));
    else Object.entries(value).forEach(([key, item]) => index(item, [...path, key]));
  };
  index(source, []);
  return paths;
}

/** Sparse IO number spellings distinguish facts already rounded by JSON.parse.
 * This is source identity for pure projection, not a second decision owner or
 * a new hash codec. Native JS callers need no metadata. Never copy full bodies
 * just to decide whether two registered carriers can share their source. */
export function contextSourceIdentity(source: JsonObject, supplied?: unknown):
    (value: unknown, path: ProjectionPath) => string {
  if (supplied === undefined) return value => JSON.stringify(canonicalAuthorityJson(value));
  const tokens = sourceNumberTokens(source, supplied);
  const paths = contextObjectPaths(source);
  return (value, path) => {
    const original = value !== null && typeof value === "object" ? paths.get(value) : undefined;
    const prefix = original ?? path;
    const relative = tokens.filter(item => prefix.every((key, i) => item.path[i] === key))
      .map(item => [item.path.slice(prefix.length), item.token]);
    // Sort encoded relative paths, so original object insertion order is not
    // mistaken for a content difference. Keep token strings separate from data.
    relative.sort((left, right) => authorityUnicodeCompare(JSON.stringify(left[0]), JSON.stringify(right[0])));
    return JSON.stringify([canonicalAuthorityJson(value), relative]);
  };
}

/** Return only display edits, never round-trip unrelated source facts through
 * JavaScript numbers. References address the immutable input, including bodies
 * moved into source_projection. Python applies these edits without policy. */
export type ProjectionSourceValues = WeakMap<object, Map<string, ProjectionPath>>;

export function contextProjectionChanges(source: JsonObject, projected: JsonObject,
    sourceValues?: ProjectionSourceValues): JsonObject {
  const paths = contextObjectPaths(source), changes: ProjectionChange[] = [];
  const visit = (before: unknown, after: unknown, path: ProjectionPath, parent?: object): void => {
    const original = parent && sourceValues?.get(parent)?.get(String(path.at(-1)));
    if (original) { changes.push({path, from: original}); return; }
    if (Object.is(before, after)) return;
    const from = after !== null && typeof after === "object" ? paths.get(after) : undefined;
    if (from) { changes.push({path, from}); return; }
    if (Array.isArray(after)) {
      const old = Array.isArray(before) ? before : [];
      if (!Array.isArray(before)) changes.push({path, value: []});
      after.forEach((value, i) => visit(old[i], value, [...path, i], after));
      // Remove from the end so earlier indices retain their original meaning.
      for (let i = old.length - 1; i >= after.length; i--) changes.push({path: [...path, i], remove: true});
    } else if (jsonObject(after)) {
      const old = jsonObject(before) ?? {};
      if (!jsonObject(before)) changes.push({path, value: {}});
      for (const key of Object.keys(old))
        if (!Object.hasOwn(after as JsonObject, key)) changes.push({path: [...path, key], remove: true});
      for (const [key, value] of Object.entries(after as JsonObject))
        visit(Object.hasOwn(old, key) ? old[key] : undefined, value, [...path, key], after as object);
    } else changes.push({path, value: after});
  };
  visit(source, projected, []);
  return {changes};
}

/** Compare a pure edit result using the same original number spellings.
 * References are IO lineage, not an alternate context/hash or decision owner. */
export function contextProjectionIdentity(source: JsonObject, projected: JsonObject,
    supplied?: unknown, sourceValues?: ProjectionSourceValues): string {
  if (supplied === undefined) return JSON.stringify(canonicalAuthorityJson(projected));
  // Validate every supplied path/token before deriving identities from it.
  contextSourceIdentity(source, supplied);
  const tokens = supplied as [ProjectionPath, string][];
  const result: [ProjectionPath, string][] = [];
  const edits = contextProjectionChanges(source, projected, sourceValues).changes as ProjectionChange[];
  for (const edit of edits) {
    if (!("from" in edit)) continue;
    for (const [path, token] of tokens) {
      if (edit.from.every((key, i) => path[i] === key))
        result.push([[...edit.path, ...path.slice(edit.from.length)], token]);
    }
  }
  return contextSourceIdentity(projected, result)(projected, []);
}

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
// The read codec adds these display fields to canonical records. Retain them
// on visible items; they do not make a known diagnostic list a future schema.
const ITEM_FIELDS = new Set<string>([...TODO_CANONICAL_READ_RECORD_FIELDS, "index", "content_revision"]);

/** Exact repeated records remain fully available in this model view. Unlike
 * diagnostic omission, this preserves every lane's membership and ordering,
 * including structured dependencies and handoffs. No relevance is inferred. */
export function quotaSummaryReferences(payload: JsonObject): JsonObject[] {
  const references: JsonObject[] = [];
  const safeNumbers = (value: unknown): boolean => typeof value === "number"
    ? Number.isSafeInteger(value)
    : value === null || typeof value !== "object" || Object.values(value).every(safeNumbers);
  for (const root of ["agent_todo_summary", "user_todo_summary"]) {
    const summary = jsonObject(payload[root]);
    if (summary?.schema_version !== "todo_summary_v0") continue;
    const seen = new Map<string, ProjectionPath>();
    for (const [lane, rows] of Object.entries(summary)) {
      if (!DIAGNOSTIC_LANES.has(lane) || !Array.isArray(rows)) continue;
      rows.forEach((value, index) => {
        const row = jsonObject(value);
        if (!row || row.schema_version !== "todo_item_v0" || typeof row.todo_id !== "string"
            || !row.todo_id || !Object.keys(row).every(key => ITEM_FIELDS.has(key)) || !safeNumbers(row)) return;
        const key = JSON.stringify(canonicalAuthorityJson(row)), path = [root, lane, index];
        const from = seen.get(key);
        if (!from) { seen.set(key, path); return; }
        // Keep the original literal as the target; references never form chains.
        const reference = {todo_id: row.todo_id, context_ref: "/" + from.join("/")};
        if (Buffer.byteLength(JSON.stringify(reference)) < Buffer.byteLength(JSON.stringify(row)))
          references.push({path, from, value: reference});
      });
    }
  }
  return references;
}

function knownItem(value: unknown): boolean {
  const scalar = (value: unknown) => value === null || ["string", "number", "boolean"].includes(typeof value);
  const item = jsonObject(value);
  return item !== null && (item.schema_version === undefined || item.schema_version === "todo_item_v0") &&
    Object.entries(item).every(([key, field]) => ITEM_FIELDS.has(key) &&
      (scalar(field) || (Array.isArray(field) && field.every(scalar))));
}

function compactSummary(summary: JsonObject, role: "agent" | "user", detail: string, captured: boolean): JsonObject {
  if (summary.schema_version !== undefined && summary.schema_version !== "todo_summary_v0") return summary;
  const limits = role === "agent" ? AGENT_LIMITS : USER_LIMITS;
  const schema = role === "agent" ? "quota_cli_todo_summary_compaction_v0" : "quota_cli_user_todo_summary_compaction_v0";
  const compact = {...summary}, previous = jsonObject(summary.payload_compaction);
  const owned = previous?.schema_version === schema;
  const omitted: JsonObject = {...(owned ? jsonObject(previous?.omitted_lanes) : {})};
  for (const [key, value] of Object.entries(summary)) {
    if (!DIAGNOSTIC_LANES.has(key) || !Array.isArray(value)) continue;
    const limit = limits[key] ?? 0;
    // Preserve each unfamiliar record verbatim, wherever it occurs. One such
    // record must not disable the existing projection for every known sibling.
    const retained = value.filter((item, i) => i < limit || !knownItem(item));
    if (limit || retained.length) compact[key] = retained;
    else delete compact[key];
    if (value.length > retained.length) omitted[key] = value.length - retained.length;
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
      if (!Array.isArray(value)) continue;
      if (parentKey === "todo_succession_warning" && !Object.hasOwn(parent, "todo_ids")) {
        const ids = value.map(item => jsonObject(item)?.todo_id).filter(id => typeof id === "string" && id).slice(0, 3);
        if (ids.length) projected.todo_ids = ids;
      }
      const retained = value.filter(item => !knownItem(item));
      if (retained.length) projected[key] = retained;
      else delete projected[key];
      if (value.length > retained.length) omitted[`${parentKey}.${key}`] = value.length - retained.length;
    }
    compact[parentKey] = projected;
  }
  const metadata: JsonObject = {...(owned ? previous : previous ? {source_projection: previous} : {}),
    schema_version: schema,
    retained_item_lanes: Object.keys(limits).filter(key => key !== "current_agent_blocker_items" ||
      (Array.isArray(summary[key]) && (summary[key] as unknown[]).length > 0)).sort(),
    omitted_lanes: omitted};
  // A removed lane has zero visible rows. Fold its known upstream truncation
  // into one total instead of describing an intermediate list no longer shown.
  // Retained lanes still need the source's text/row truncation evidence. Unknown
  // schemas, fields or inconsistent counts remain verbatim for diagnosis.
  if (!owned && previous?.schema_version === "quota_todo_summary_payload_compaction_v0") {
    const lanes = jsonObject(previous.compacted_lanes);
    if (lanes) {
      const remaining = {...lanes};
      for (const [key, value] of Object.entries(lanes)) {
        const counts = jsonObject(value), rows = summary[key];
        if (Object.hasOwn(compact, key) || !Array.isArray(rows) || !counts ||
            Object.keys(counts).some(field => field !== "shown" && field !== "total") ||
            !Number.isSafeInteger(counts.shown) || !Number.isSafeInteger(counts.total) ||
            counts.shown !== rows.length || (counts.total as number) < rows.length ||
            omitted[key] !== rows.length) continue;
        omitted[key] = counts.total;
        delete remaining[key];
      }
      metadata.source_projection = {...previous, compacted_lanes: remaining};
    }
  }
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
