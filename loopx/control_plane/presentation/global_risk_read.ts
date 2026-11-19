import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireInteger, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {sealProjectionEnvelope} from "../projection_envelope.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {normalizeTodoAgent, stripPythonWhitespace, compactPythonWhitespace} from "../coordination/todo_agents.ts";

const COMMAND = "/loopx-global-risks";
const CONTRACT = "status.contract.error_diagnostics";
const REGISTRY = "status.global_registry.findings";
const STALE = "status.attention_queue.items.stale_latest_run_warning";
const POLL = "project.host_poll_receipts";
const SEVERITY: Record<string, number> = {high: 0, action: 1, warning: 2, info: 3};
const CATEGORY: Record<string, number> = {boundary_warning: 0, failing_check: 1, stale_run: 2};
export const SOURCE_SURFACES = ["status contract diagnostics", "global registry health findings",
  "status attention queue stale-run warnings", "status compact run-history coordination"] as const;
const object = (value: unknown): JsonObject => jsonObject(value) ?? {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const truth = (value: unknown): boolean => value !== null && value !== undefined && value !== false && value !== 0 && value !== "" &&
  (!Array.isArray(value) || value.length > 0) && (!jsonObject(value) || Object.keys(object(value)).length > 0);
function pyString(value: unknown, nested = false): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "string") return nested ? `'${value.replaceAll("\\", "\\\\").replaceAll("'", "\\'")}'` : value;
  if (Array.isArray(value)) return `[${value.map(item => pyString(item, true)).join(", ")}]`;
  if (jsonObject(value)) return `{${Object.entries(object(value)).map(([key, item]) => `${pyString(key, true)}: ${pyString(item, true)}`).join(", ")}}`;
  return String(value);
}
const text = (value: unknown): string => stripPythonWhitespace(pyString(truth(value) ? value : ""));

// Mirrors loopx/public_safe_text.py PRESENTATION_LOCAL_PATH_PATTERNS; guarded by parity test.
export const LOCAL_PATH_PATTERNS = [
  /\/(?:Users|home|private|tmp|var)\/[^\s`|,)]+/gu,
  /(?<![:/A-Za-z0-9])(?:\/(?:Users|home|Volumes|private|tmp|var|etc|opt|srv|mnt|root|data|workspace|workspaces)\/[^\s`'"<>]+|[A-Za-z]:[\\/][^\s`'"<>]+|\\\\[A-Za-z0-9_.-]+\\[^\s`'"<>]+)/giu,
  /\\\\\?\\(?:UNC\\[A-Za-z0-9_.-]+\\|Volume\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}\\|GLOBALROOT\\Device\\)[^\s`|,)]+/giu,
  /(?<=:)(?:[A-Za-z]:[\\/][^\s`|,)]+|\\\\\?\\(?:UNC\\[A-Za-z0-9_.-]+\\|Volume\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}\\)[^\s`|,)]+|\\\\\?\\GLOBALROOT\\Device\\[^\s`|,)]+|\\\\[A-Za-z0-9_.-]+\\[^\s`|,)]+)/giu,
];

// Presentation codec parity includes Python whitespace and code-point limits.
function redact(value: unknown, limit = 260): string {
  let result = text(value).replaceAll("/loop-global-risks", COMMAND);
  for (const pattern of LOCAL_PATH_PATTERNS) result = result.replace(pattern, "<local-path-redacted>");
  result = compactPythonWhitespace(result);
  const chars = Array.from(result);
  return chars.length > limit ? stripPythonWhitespace(chars.slice(0, Math.max(0, limit - 1)).join("")) + "…" : result;
}
function boundary(): JsonObject {
  return Object.fromEntries(["raw_logs_recorded", "raw_transcripts_recorded", "raw_connector_payloads_recorded",
    "credential_values_recorded", "absolute_paths_recorded", "private_source_bodies_recorded"].map(key => [key, false]));
}
function timeRange(value: unknown): string {
  const normalized = text(value || "24h").toLowerCase();
  return /^\d+[hd]$/u.test(normalized) && Number(normalized.slice(0, -1)) > 0 ? normalized : "24h";
}
function request(value: unknown): JsonObject {
  return {schema_version: "global_manager_command_request_v0", command: COMMAND,
    legacy_aliases: ["/loop-global-risks"], cli_command: "loopx global-risks", time_range: timeRange(value),
    include: ["stale_runs", "boundary_warnings", "failing_checks", "rollback_candidates"],
    privacy_mode: "public_safe_summary", dry_run: true};
}
function warning(code: string, source: string, extras: JsonObject = {}): JsonObject {
  const result: JsonObject = {reason_code: redact(code, 120)};
  if (source) result.source = redact(source, 160);
  for (const key of ["goal_id", "detail"]) if (truth(extras[key])) result[key] = redact(extras[key], key === "goal_id" ? 120 : 260);
  for (const key of ["available_count", "inspected_count"]) if (extras[key] != null) result[key] = extras[key];
  return result;
}
function occurrence(source: string, index: number, kind: string, scope: string, goal: string | null): string {
  const identity = {goal_id: goal, kind, scope, source_index: index, source_surface: source};
  const encoded = JSON.stringify(identity).replace(/[\u007f-\uffff]/gu, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
  return createHash("sha256").update(encoded).digest("hex").slice(0, 16);
}
function severity(value: unknown, fallback = "warning"): string {
  const normalized = text(value);
  return normalized === "error" ? "high" : Object.hasOwn(SEVERITY, normalized) ? normalized : fallback;
}
function contractRows(raw: unknown, index: number, warnings: JsonObject[]): JsonObject[] {
  const diagnostic = jsonObject(raw);
  if (!diagnostic) { warnings.push(warning("malformed_contract_diagnostic", CONTRACT)); return []; }
  const code = text(diagnostic.code);
  if (!code) { warnings.push(warning("contract_code_missing", CONTRACT)); return []; }
  const scope = text(diagnostic.scope || "global") || "global", goal = text(diagnostic.goal_id);
  const goals: (string | null)[] = [];
  if (scope !== "goals") goals.push(goal || null);
  else {
    if (diagnostic.goal_ids != null && !Array.isArray(diagnostic.goal_ids)) warnings.push(warning("malformed_contract_goal_ids", CONTRACT));
    for (const rawId of list(diagnostic.goal_ids)) {
      const id = text(rawId);
      if (id && !goals.includes(id)) goals.push(id);
    }
    if (goal && !goals.includes(goal)) goals.push(goal);
    if (!goals.length) warnings.push(warning("contract_goal_identity_missing", CONTRACT));
  }
  return goals.map(id => {
    const hash = occurrence(CONTRACT, index, code, scope, id);
    const action = code === "public_boundary_violation" ? "Inspect and remove the boundary violation before delivery."
      : code === "registry_boundary_risk" ? "Inspect and repair the registry boundary projection."
      : `Inspect and resolve contract check ${code} before relying on this goal.`;
    return {goal_id: redact(id, 120) || null, scope: redact(scope, 120),
      category: ["public_boundary_violation", "registry_boundary_risk"].includes(code) ? "boundary_warning" : "failing_check",
      kind: redact(code, 120), severity: severity(diagnostic.severity, "high"),
      summary: redact(diagnostic.message) || `Contract check ${code} failed.`, occurrence_id: hash, occurrence_count: 1,
      evidence_refs: [`${CONTRACT}:${code}:${hash}`], next_safe_action: action, requires_user_approval: false};
  });
}
function registryRows(raw: unknown, index: number, warnings: JsonObject[]): JsonObject[] {
  const finding = jsonObject(raw);
  if (!finding) { warnings.push(warning("malformed_registry_finding", REGISTRY)); return []; }
  const level = text(finding.severity);
  if (!["high", "action"].includes(level)) return [];
  const kind = text(finding.kind);
  if (!kind) { warnings.push(warning("registry_kind_missing", REGISTRY)); return []; }
  const goal = text(finding.goal_id) || null, scope = goal ? "goal" : "global";
  const hash = occurrence(REGISTRY, index, kind, scope, goal);
  return [{goal_id: redact(goal, 120) || null, scope, category: "failing_check", kind: redact(kind, 120), severity: level,
    summary: redact(finding.message) || `Global registry finding ${kind} requires attention.`, occurrence_id: hash, occurrence_count: 1,
    evidence_refs: [`${REGISTRY}:${kind}:${hash}`],
    next_safe_action: redact(finding.recommended_action) || `Inspect and resolve global registry finding ${kind}.`, requires_user_approval: false}];
}
function timestamp(value: unknown): string | null {
  const stamp = text(value);
  return stamp && parseTodoTimestampMicros(stamp) !== null && !stamp.endsWith("z") ? stamp : null;
}
function staleRows(raw: unknown, index: number, warnings: JsonObject[]): JsonObject[] {
  const item = jsonObject(raw);
  if (!item) { warnings.push(warning("malformed_attention_item", STALE)); return []; }
  if (item.stale_latest_run_warning == null) return [];
  const stale = jsonObject(item.stale_latest_run_warning);
  if (!stale) { warnings.push(warning("malformed_stale_warning", STALE)); return []; }
  const kind = text(stale.kind);
  if (kind !== "stale_latest_run_projection") { warnings.push(warning("invalid_stale_warning_kind", STALE)); return []; }
  const goal = text(item.goal_id) || null, scope = goal ? "goal" : "global";
  const hash = occurrence(STALE, index, kind, scope, goal), reason = redact(stale.reason, 160);
  const row: JsonObject = {goal_id: redact(goal, 120) || null, scope, category: "stale_run", kind,
    severity: severity(stale.severity), summary: reason || "Latest-run routing is stale relative to active state.", reason: reason || null,
    occurrence_id: hash, occurrence_count: 1, evidence_refs: [`${STALE}:${kind}:${hash}`],
    next_safe_action: "Run refresh-state before trusting latest-run routing.", requires_user_approval: false};
  for (const field of ["latest_run_generated_at", "active_state_updated_at"]) {
    const stamp = timestamp(stale[field]);
    if (stamp === null) warnings.push(warning("invalid_stale_timestamp", STALE, {goal_id: goal, detail: `Structured stale warning has no valid ${field}.`}));
    else row[field] = stamp;
  }
  return [row];
}
function pollRows(host: JsonObject, now: string, warnings: JsonObject[], scan: number): {
  rows: JsonObject[]; status: string; count: number | null; omitted: number; unreadable: number; refs: string[];
} {
  const result = {rows: [] as JsonObject[], status: "missing", count: null as number | null, omitted: 0, unreadable: 0, refs: [] as string[]};
  if (host.error != null) {
    warnings.push(warning("host_poll_registry_unreadable", POLL, {detail: Array.from(pyString(host.error)).slice(0, 200).join("")}));
    return {...result, status: "unreadable", unreadable: 1};
  }
  if (host.read_status === "missing") return result;
  if (!Array.isArray(host.goals)) return {...result, status: "unreadable", unreadable: 1};
  result.status = "read"; result.count = 0; result.omitted = Math.max(0, requireInteger(host.goal_count, "host_poll.goal_count") - scan);
  for (const [index, raw] of host.goals.slice(0, scan).entries()) {
    const entry = object(raw), goal = object(entry.goal), observation = object(entry.observation);
    if (!truth(goal.id)) continue;
    if (observation.read_status === "missing") continue;
    const ref = redact(pyString(goal.id || ""), 120), receipt = jsonObject(observation.receipt);
    if (observation.read_status !== "read" || !receipt) {
      result.unreadable++;
      if (ref && result.refs.length < 8) result.refs.push(ref);
      continue;
    }
    result.count++;
    if (receipt.expected_continuation !== true || typeof receipt.last_poll_at !== "string") continue;
    const last = timestamp(receipt.last_poll_at), current = parseTodoTimestampMicros(now);
    if (!last || current === null || current - parseTodoTimestampMicros(last)! < 240n * 60n * 1000000n) continue;
    const reason = redact("The host loop polled quota while expecting continuation but has gone quiet for longer than the staleness window. The driver may have died mid-wait, or the loop may have been paused or stopped intentionally; check the session and LoopX gates before resuming.", 200);
    const id = ref || null, kind = "stale_host_poll";
    result.rows.push({goal_id: id, scope: "goal", category: "stale_run", kind, severity: "warning", summary: reason, reason,
      occurrence_id: occurrence(POLL, index, kind, "goal", id), occurrence_count: 1,
      evidence_refs: [`${POLL}:${kind}:${receipt.last_poll_at}`],
      next_safe_action: "Check the host session and LoopX gates; restart the goal worker or resume the bridge when the loop should continue.", requires_user_approval: true});
  }
  return result;
}
function bounded(rows: unknown[], source: string, scan: number, warnings: JsonObject[]): unknown[] {
  if (rows.length > scan) warnings.push(warning("source_rows_truncated", source, {available_count: rows.length, inspected_count: scan}));
  return rows.slice(0, scan);
}
function filterAgent(rows: JsonObject[], status: JsonObject, agent: string | null, scan: number, warnings: JsonObject[]): [JsonObject[], boolean] {
  if (!agent) return [rows, false];
  const history = jsonObject(status.run_history);
  if (!history) throw new Error("compact run-history projection is missing or malformed");
  if (!Array.isArray(history.goals)) throw new Error("compact run-history goal list is missing or malformed");
  const goals = new Map<string, JsonObject>();
  for (const raw of bounded(history.goals, "status.run_history.goals", scan, warnings)) {
    const goal = object(raw), id = text(goal.id);
    if (id && !goals.has(id)) goals.set(id, goal);
  }
  return [rows.filter(row => {
    const id = text(row.goal_id);
    if (!id) return true;
    const goal = goals.get(id);
    if (!goal) throw new Error(`agent scope unavailable for goal ${id}`);
    const registered = object(goal.coordination).registered_agents;
    if (!Array.isArray(registered)) throw new Error("agent scope unavailable for goal");
    return registered.some(raw => {
      const item = jsonObject(raw);
      const value = item ? item.id || item.agent_id || item.name : raw;
      try { return normalizeTodoAgent(compactPythonWhitespace(pyString(value || "")), "agent") === agent; }
      catch { return false; }
    });
  }), history.goals.length > scan];
}
const count = (value: unknown): number => typeof value === "number" && Number.isInteger(value) && value > 0 ? value : 1;
export function aggregateGlobalRisks(rows: JsonObject[]): JsonObject[] {
  const result = new Map<string, JsonObject>();
  for (const row of rows) {
    const key = JSON.stringify([row.category, row.kind, row.goal_id || "", row.occurrence_id].map(value => pyString(value || "")));
    const existing = result.get(key);
    if (!existing) result.set(key, {...row, occurrence_count: count(row.occurrence_count), evidence_refs: list(row.evidence_refs).map(item => pyString(item)).filter(Boolean)});
    else {
      existing.occurrence_count = count(existing.occurrence_count) + count(row.occurrence_count);
      if ((SEVERITY[text(row.severity)] ?? 4) < (SEVERITY[text(existing.severity)] ?? 4)) existing.severity = row.severity;
      existing.evidence_refs = [...new Set([...list(existing.evidence_refs), ...list(row.evidence_refs)])];
    }
  }
  return [...result.values()];
}
function compare(left: JsonObject, right: JsonObject): number {
  const key = (row: JsonObject): (number | string)[] => [SEVERITY[text(row.severity)] ?? 4, CATEGORY[text(row.category)] ?? 3,
    truth(row.goal_id) ? 1 : 0, text(row.goal_id), text(row.kind), pyString(list(row.evidence_refs)[0] ?? "")];
  const a = key(left), b = key(right);
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) {
    if (typeof a[i] === "number" && typeof b[i] === "number") return Number(a[i]) - Number(b[i]);
    const left = Array.from(String(a[i]), char => char.codePointAt(0)!);
    const right = Array.from(String(b[i]), char => char.codePointAt(0)!);
    for (let j = 0; j < Math.min(left.length, right.length); j++) if (left[j] !== right[j]) return left[j] - right[j];
    return left.length - right.length;
  }
  return 0;
}
function groups(rows: JsonObject[]): JsonObject {
  return {stale_runs: rows.filter(row => row.category === "stale_run"), boundary_warnings: rows.filter(row => row.category === "boundary_warning"),
    failing_checks: rows.filter(row => row.category === "failing_check"), rollback_candidates: []};
}
function risksEnvelope(status: JsonObject, now: string, readAt: string | null,
  poll: ReturnType<typeof pollRows>, omittedRows: number, shown: number, availableCount: number, agent: string | null): JsonObject {
  const registry = object(status.global_registry), upstream = object(status.projection_envelope);
  const globalSource = object(list(upstream.sources).find(raw => object(raw).source_id === "global_registry"));
  const available = registry.available === true;
  const expected = available && typeof registry.global_goal_count === "number" && Number.isInteger(registry.global_goal_count) ? registry.global_goal_count : null;
  const excluded = available ? Number(registry.current_registry_excluded_goal_count || 0) : 0;
  const omitted: JsonObject[] = [];
  if (excluded) omitted.push({reason: "outside_current_registry", count: excluded, refs: list(registry.current_registry_excluded_goal_ids).slice(0, 8).map(item => pyString(item))});
  if (omittedRows) omitted.push({reason: "source_rows_not_scanned", count: omittedRows});
  if (poll.omitted) omitted.push({reason: "host_poll_goals_not_scanned", count: poll.omitted});
  if (poll.unreadable) omitted.push({reason: "host_poll_receipts_unreadable", count: poll.unreadable, refs: poll.refs.slice(0, 8)});
  if (truth(status) && status.ok !== true) omitted.push({reason: "status_unavailable", count: 1});
  const contract = object(status.contract), queue = object(status.attention_queue), history = object(status.run_history);
  const contractRead = !!jsonObject(status.contract) && (!Object.hasOwn(contract, "error_diagnostics") || Array.isArray(contract.error_diagnostics));
  const queueRead = !!jsonObject(status.attention_queue) && (!Object.hasOwn(queue, "items") || Array.isArray(queue.items));
  const source = (id: string, readStatus: unknown, at: unknown, itemCount: unknown, unreadable = 0): JsonObject => ({source_id: id,
    read_status: readStatus, last_read_at: at, required: true, source_updated_at: null, item_count: itemCount, missing_count: 0, unreadable_count: unreadable});
  const sources = [source("global_registry", available ? globalSource.read_status || "read" : registry.read_status || (readAt ? "missing" : "not_read"),
    globalSource.last_read_at || (available ? readAt : null), expected),
    source("status_contract", contractRead ? "read" : "not_read", contractRead ? readAt : null, contractRead ? list(contract.error_diagnostics).length : null),
    source("attention_queue", queueRead ? "read" : "not_read", queueRead ? readAt : null, queueRead ? list(queue.items).length : null),
    source("host_poll_receipts", poll.status, poll.status === "read" ? now : null, poll.count, poll.unreadable)];
  if (!truth(upstream)) sources.push(source("status", "not_read", null, null));
  if (agent) sources.push(source("agent_scope", Array.isArray(history.goals) ? "read" : "not_read",
    Array.isArray(history.goals) ? readAt : null, truth(history) ? list(history.goals).length : null));
  return sealProjectionEnvelope({schema_version: "loopx_projection_envelope_seal_request_v0", projection: "global_risks", observed_at: now,
    sources, coverage: {scope: "global", expected_count: expected, included_count: expected === null ? 0 : Math.max(0, expected - excluded),
      omitted, shown_count: shown, available_count: availableCount}, upstream: truth(upstream) ? [upstream] : []});
}
function errorResult(error: unknown, code: unknown, range: unknown, status: JsonObject, now: string, readAt: string | null): JsonObject {
  const payload: JsonObject = {ok: false, schema_version: "global_manager_command_response_v0", generated_at: now,
    request: request(range), error_code: redact(code, 120), error: redact(error),
    omissions: ["Raw/private failure details and local paths were intentionally omitted."], boundary: boundary()};
  try { payload.projection_envelope = risksEnvelope(status, now, readAt,
    {rows: [], status: "not_read", count: null, omitted: 0, unreadable: 0, refs: []}, 0, 0, 0, null); }
  catch { /* Failure reporting remains available when envelope facts are invalid. */ }
  return payload;
}

export function projectGlobalRisks(input: JsonObject): JsonObject {
  if (input.schema_version !== "loopx_global_risks_read_v0") throw new TypeError("unsupported global risks read schema");
  const now = requireNonEmptyString(input.observed_at, "observed_at");
  if (parseTodoTimestampMicros(now, true) === null) throw new TypeError("observed_at must be an aware timestamp");
  const limit = Math.min(100, Math.max(1, requireInteger(input.limit, "limit")));
  const scan = Math.min(400, Math.max(limit * 4, 40));
  if (input.agent_id !== null && typeof input.agent_id !== "string") throw new TypeError("agent_id must be a string or null");
  if (typeof input.time_range !== "string") throw new TypeError("time_range must be a string");
  const agent = input.agent_id || null, range = timeRange(input.time_range);
  const status = jsonObject(input.status);
  const readAt = status ? now : null;
  if (input.error != null) return errorResult(input.error, input.error_code || "global_risks_unavailable", range, status ?? {}, now, input.status_read_at == null ? null : now);
  if (!status) return errorResult("Global status payload is not an object.", "malformed_status_projection", range, {}, now, null);
  let diagnostics: unknown[], findings: unknown[], items: unknown[];
  try {
    const required = (key: string): JsonObject => {
      const value = jsonObject(status[key]);
      if (!value) throw new Error(`Required status projection ${key} is missing or malformed.`);
      return value;
    };
    const contract = required("contract"), registry = required("global_registry"), queue = required("attention_queue");
    const optional = (container: JsonObject, key: string): unknown[] => {
      if (!Object.hasOwn(container, key)) return [];
      if (!Array.isArray(container[key])) throw new Error(`Required status source list ${key} is malformed.`);
      return container[key];
    };
    diagnostics = optional(contract, "error_diagnostics"); findings = optional(registry, "findings"); items = optional(queue, "items");
  } catch (error) { return errorResult((error as Error).message, "malformed_status_projection", range, status, now, readAt); }
  const warnings: JsonObject[] = [];
  let rows = bounded(diagnostics, CONTRACT, scan, warnings).flatMap((raw, index) => contractRows(raw, index, warnings));
  let truncated = diagnostics.length > scan;
  if (object(status.global_registry).available === false) warnings.push(warning("global_registry_unavailable", "status.global_registry"));
  else {
    rows.push(...bounded(findings, REGISTRY, scan, warnings).flatMap((raw, index) => registryRows(raw, index, warnings)));
    truncated ||= findings.length > scan;
  }
  rows.push(...bounded(items, STALE, scan, warnings).flatMap((raw, index) => staleRows(raw, index, warnings)));
  truncated ||= items.length > scan;
  const host = requireJsonObject(input.host_poll, "host_poll");
  const poll = pollRows(host, now, warnings, scan);
  rows.push(...poll.rows); truncated ||= poll.omitted > 0;
  try {
    const filtered = filterAgent(rows, status, agent, scan, warnings);
    rows = filtered[0]; truncated ||= filtered[1];
  } catch (error) { return errorResult((error as Error).message, "agent_scope_unavailable", range, status, now, readAt); }
  const aggregated = aggregateGlobalRisks(rows).sort(compare), retained = aggregated.slice(0, limit), fullGroups = groups(aggregated);
  return {ok: true, schema_version: "global_manager_command_response_v0", generated_at: now, request: request(range),
    summary: {source_health_ok: status.ok === true, matched_risk_count: aggregated.length,
      matched_occurrence_count: aggregated.reduce((sum, row) => sum + count(row.occurrence_count), 0), returned_risk_count: retained.length,
      source_scan_limit: scan, source_rows_truncated: truncated, stale_run_count: list(fullGroups.stale_runs).length,
      boundary_warning_count: list(fullGroups.boundary_warnings).length, failing_check_count: list(fullGroups.failing_checks).length,
      rollback_candidate_count: 0, rollback_candidates_overlap_risks: false, truncated: aggregated.length > retained.length,
      source_warning_count: warnings.length, source_surfaces: SOURCE_SURFACES},
    groups: groups(retained), risks: retained, source_warnings: warnings.slice(0, 8), source_warnings_truncated: warnings.length > 8,
    projection_envelope: risksEnvelope(status, now, readAt, poll,
      warnings.filter(row => row.reason_code === "source_rows_truncated").reduce((sum, row) => sum + Math.max(0, Number(row.available_count || 0) - Number(row.inspected_count || 0)), 0),
      retained.length, aggregated.length, agent),
    omissions: [{kind: "rollback_candidate_source_unavailable", reason: "Current projections do not carry rollback trigger and causal linkage."}], boundary: boundary()};
}
