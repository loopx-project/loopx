/** Compatibility codecs for canonical and historical Todo read records. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthorityJson, canonicalAuthoritySha256, authorityUnicodeCompare, AuthorityStoreProtocolError} from "../coordination/authority_store_codec.ts";
import {COORDINATION_STATE_CONTRACT} from "../coordination/coordination_state_contract.generated.ts";
import {compactPythonWhitespace, stripPythonWhitespace, normalizeTodoAgent} from "../coordination/todo_agents.ts";
import {legacyTodoPriority} from "./priority.ts";
import {normalizeTodoNoFollowup} from "./completion_state.ts";
import {normalizeTodoResumeWhen, TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION} from "./resume_condition.ts";
import {normalizeTodoDecisionScope} from "./decision_metadata.ts";
import {normalizeTodoCapabilities, normalizeTodoRepository} from "./work_requirements.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import codec from "./read_legacy_codec.json" with {type: "json"};

export const recordFields = COORDINATION_STATE_CONTRACT.todo_read_record.fields;
const classes = ["advancement_task", "continuous_monitor", "user_gate", "user_action", "blocker"];
export const id = (value: unknown): string | null => {
  const candidate = typeof value === "string" ? stripPythonWhitespace(value).toLowerCase() : "";
  return /^todo_[a-z0-9_-]{3,64}$/.test(candidate) ? candidate : null;
};
export const object = (value: unknown): JsonObject => value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
export const text = (value: unknown): string => value == null ? "" : String(value);
export function ids(value: unknown): string[] {
  const values = Array.isArray(value) ? value : [value];
  const found = values.flatMap(raw => (typeof raw === "object" && raw !== null ? JSON.stringify(raw) : text(raw || ""))
    .match(new RegExp(boundary + "todo_[A-Za-z0-9_-]+" + boundary, "gu")) ?? []);
  return [...new Set(found.map(id).filter((v): v is string => v !== null))];
}
const hash = (value: string): string => createHash("sha256").update(value).digest("hex");
export const withoutNull = (value: JsonObject): JsonObject => Object.fromEntries(Object.entries(value).filter(([, v]) => v != null));

export function compactTodoReadText(value: string, limit: number | null = 500): string {
  const full = compactPythonWhitespace(value), points = Array.from(full);
  return limit === null || points.length <= limit ? full : stripPythonWhitespace(points.slice(0, limit - 1).join("")) + "…";
}
export function pythonJson(value: unknown): string {
  return JSON.stringify(canonicalAuthorityJson(value)).replace(/[\u007f-\uffff]/g,
    ch => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"));
}
const boundary = "(?:(?<=[\\p{L}\\p{N}_])(?![\\p{L}\\p{N}_])|(?<![\\p{L}\\p{N}_])(?=[\\p{L}\\p{N}_]))";
const patterns = (values: string[]) => values.map(pattern => new RegExp(pattern.replace("(?i)", "").replaceAll("\\b", boundary).replaceAll("\\s", " "), "iu"));
const classification = [
  [patterns(codec.TODO_HARD_MONITOR_PATTERNS), "continuous_monitor"],
  [patterns(codec.TODO_ADVANCEMENT_OVERRIDE_PATTERNS), "advancement_task"],
  [patterns(codec.TODO_BLOCKED_MONITOR_PATTERNS), "continuous_monitor"],
  [patterns(codec.TODO_MONITOR_PATTERNS), "continuous_monitor"],
] as const;

export function taskClass(item: JsonObject): string {
  const explicit = text(item.task_class).trim().toLowerCase();
  if (classes.includes(explicit)) return explicit;
  const action = text(item.action_kind).trim().toLowerCase();
  if (codec.advancement_actions.includes(action)) return "advancement_task";
  if (codec.monitor_actions.includes(action)) return "continuous_monitor";
  const label = compactPythonWhitespace(text(item.text)).replace(/[\u0130\u0131]/gu, "i");
  for (const [rules, kind] of classification) if (rules.some(rule => rule.test(label))) return kind;
  return "advancement_task";
}

export function readAgent(value: unknown): string | null {
  if (value == null || value === "" || value === false) return null;
  try {return normalizeTodoAgent(typeof value === "boolean" ? value ? "True" : "" : value, "agent");}
  catch (error) {if (error instanceof AuthorityStoreProtocolError) return null; throw error;}
}
export function readExcludedAgents(value: unknown): string[] {
  const values = typeof value === "string" ? value.split(",") : Array.isArray(value) ? value : [value];
  return [...new Set(values.map(readAgent).filter((v): v is string => v !== null))].sort(authorityUnicodeCompare);
}
export const readNoFollowup = (value: unknown): boolean | null => normalizeTodoNoFollowup(typeof value === "boolean" ? value : text(value || ""));
export function readResume(value: unknown): string | null {
  const candidate = compactPythonWhitespace(text(value || ""));
  const known = normalizeTodoResumeWhen({schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION, resume_when: candidate});
  if (known) return known;
  const lowered = candidate.toLowerCase();
  return !lowered.startsWith("resume_at:") && /^[a-z][a-z0-9_-]{0,31}(?::[a-z0-9_.:@-]{1,96})?$/.test(lowered) ? lowered : null;
}

export function readScope(value: unknown): JsonObject | null {
  if (value == null || value === "") return null;
  const raw = typeof value === "string" ? value : object(value);
  const selected = typeof raw === "string" ? raw : {kind: raw.kind, granularity: raw.granularity, scope_key: raw.scope_key,
    ...(id(raw.decision_id) ? {decision_id: id(raw.decision_id)} : {})};
  try {return normalizeTodoDecisionScope(selected);}
  catch (error) {if (error instanceof EffectRuntimeRequestError) return null; throw error;}
}
function readScopes(value: unknown): JsonObject[] {
  const values = listValues(value), result: JsonObject[] = [];
  for (const raw of values) {
    const scope = readScope(raw);
    if (scope && !result.some(row => row.kind === scope.kind && row.granularity === scope.granularity && row.scope_key === scope.scope_key)) result.push(scope);
  }
  return result;
}

function listValues(value: unknown): unknown[] {
  return Array.isArray(value) ? value : text(value || "").split(/[,;|]/u).filter(Boolean);
}
function readCapabilities(value: unknown): string[] {
  const result: string[] = [];
  for (const raw of listValues(value)) {
    try {
      for (const token of normalizeTodoCapabilities([scalarText(raw)], "capability")) if (!result.includes(token)) result.push(token);
    } catch (error) {if (!(error instanceof EffectRuntimeRequestError)) throw error;}
  }
  return result;
}
function writeScopes(value: unknown): string[] {
  const scopes = listValues(value).map(raw => compactPythonWhitespace(scalarText(raw))).filter(scope => scope &&
    Array.from(scope).length <= 160 && !scope.startsWith("/") && !scope.startsWith("~") && !scope.split("/").includes("..") && !/[ <>]/u.test(scope));
  return [...new Set(scopes)];
}

const truthy = (v: unknown): boolean => Array.isArray(v) ? v.length > 0 : v !== null && typeof v === "object" ? Object.keys(v).length > 0 : Boolean(v);
function repr(value: unknown): string {
  if (value == null) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (Array.isArray(value)) return "[" + value.map(repr).join(", ") + "]";
  if (typeof value === "object") return "{" + Object.entries(value).map(([k, v]) => `${repr(k)}: ${repr(v)}`).join(", ") + "}";
  if (typeof value !== "string") return String(value);
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  return quote + Array.from(value).map(point => {
    if (point === quote || point === "\\") return "\\" + point;
    if (point === "\n") return "\\n";
    if (point === "\r") return "\\r";
    if (point === "\t") return "\\t";
    if (point !== " " && /[\p{C}\p{Z}]/u.test(point)) {
      const code = point.codePointAt(0)!;
      return code <= 255 ? "\\x" + code.toString(16).padStart(2, "0") : code <= 65535 ?
        "\\u" + code.toString(16).padStart(4, "0") : "\\U" + code.toString(16).padStart(8, "0");
    }
    return point;
  }).join("") + quote;
}
const scalarText = (value: unknown): string => !truthy(value) ? "" : typeof value === "string" ? value : repr(value);

export function handoffSource(item: JsonObject): JsonObject {
  const refs = (value: unknown): JsonObject => ({...(truthy(value) ? {truthy: true} : {}),
    ...(Array.isArray(value) && value.length ? {values: value.map(scalarText)} : {})});
  const nestedMetadata: JsonObject = {}, nested: JsonObject = {};
  for (const key of ["handoff", "handoff_note"]) {
    const value = item[key], row = object(value);
    if (truthy(value)) nested[key] = {truthy: true, fields: value !== null && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(Object.entries(row).map(([k, v]) => [k, scalarText(v)])) : null};
    nestedMetadata[key] = {from_agent: readAgent(truthy(row.from_agent) ? row.from_agent : item.agent_id),
      to_agent: readAgent(row.to_agent), evidence_refs: refs(row.evidence_refs)};
  }
  const fields = ["continuation_hint", "suggested_next_action", "note", "reason", "summary", "intent", "blocked_on", "evidence", "title", "text", "task_class", "goal_id", "source", "latest_event_kind"];
  const action = compactPythonWhitespace(scalarText(item.action_kind)).toLowerCase();
  const legacy = "(" + [item.goal_id, item.role, item.index, truthy(item.text) ? item.text : item.title].map(repr).join(", ") + ")";
  return {...nested, nested_metadata: nestedMetadata,
    texts: Object.fromEntries(fields.filter(key => truthy(item[key])).map(key => [key, scalarText(item[key])])),
    metadata: {todo_id: id(item.todo_id), claimed_by: readAgent(item.claimed_by), excluded_agents: readExcludedAgents(item.excluded_agents),
      successor_todo_ids: ids(item.successor_todo_ids), unblocks_todo_id: id(item.unblocks_todo_id), superseded_by: id(item.superseded_by),
      action_kind: /^[a-z][a-z0-9_-]{0,63}$/.test(action) ? action : null, resume_when: readResume(item.resume_when),
      required_decision_scopes: readScopes(item.required_decision_scopes), decision_scope: readScope(item.decision_scope)},
    evidence_refs: refs(item.evidence_refs), has_evidence: truthy(item.evidence), has_note: truthy(item.note),
    goal_id: "", source: "", legacy_id: id(item.todo_id) ? null : "handoff_" + createHash("sha1").update(legacy).digest("hex").slice(0, 12)};
}

/** Provider records are already decoded. This changes presentation, never authority. */
export function canonicalTodoReadItems(values: JsonObject[]): JsonObject[] {
  return values.map((value, offset) => {
    const archived = value.archive_state === "archive", native = value.schema_version === "todo_domain_record_v0";
    const row = {...value, ...(archived ? {source_section: "Completed Work Archive", index: offset + 1} : {}),
      ...(native ? {schema_version: "todo_item_v0", source_section: archived ? "Completed Work Archive" :
        value.role === "agent" ? "Agent Todo" : "User Todo / Owner Review Reading Queue", index: offset + 1} : {})};
    return row;
  });
}

export function structureCanonicalTodo(item: JsonObject, role: unknown, section: unknown,
  limit: number | null = 500, revision = true): JsonObject {
  const full = compactPythonWhitespace(text(item.text)), body = compactTodoReadText(full, limit);
  const normalizedStatus = stripPythonWhitespace(text(item.status)).toLowerCase();
  const status = ["open", "blocked", "done", "deferred"].includes(normalizedStatus) ? normalizedStatus : item.done ? "done" : "open";
  const parsed = legacyTodoPriority(body);
  return {...readMetadata(item), schema_version: "todo_item_v0", role, source_section: section,
    archive_state: item.archive_state ?? "active", status, done: status === "done" || status === "deferred",
    text: body, task_class: taskClass({...item, text: body}),
    ...(revision && full ? {content_revision: "sha256:" + hash(full)} : {}),
    ...(parsed.priority ? {priority: parsed.priority, title: compactTodoReadText(parsed.title, limit)} : {})};
}

function readMetadata(item: JsonObject): JsonObject {
  const result = {...item};
  for (const key of ["created_by", "claimed_by", "bound_agent", "blocks_agent"]) {
    const normalized = readAgent(item[key]);
    if (normalized !== null) result[key] = normalized;
  }
  for (const key of ["global_gate", "goal_bound"]) {
    const value = item[key];
    if (typeof value === "boolean") continue;
    const token = compactPythonWhitespace(text(value || "")).toLowerCase();
    if (["1", "true", "yes", "y", "global_gate", "global-gate"].includes(token)) result[key] = true;
    else if (["0", "false", "no", "n"].includes(token)) result[key] = false;
  }
  const excluded = readExcludedAgents(item.excluded_agents);
  if (excluded.length) result.excluded_agents = excluded;
  const resume = readResume(item.resume_when);
  if (resume !== null) result.resume_when = resume;
  for (const key of ["required_capabilities", "target_capabilities"]) {
    const values = readCapabilities(item[key]);
    if (values.length) result[key] = values;
  }
  const scopes = writeScopes(item.required_write_scopes);
  if (scopes.length) result.required_write_scopes = scopes;
  const decision = readScope(item.decision_scope), required = readScopes(item.required_decision_scopes);
  if (decision) result.decision_scope = decision;
  if (required.length) result.required_decision_scopes = required;
  const outcome = compactPythonWhitespace(scalarText(item.decision_outcome)).toLowerCase();
  if (["approve", "reject", "cancel"].includes(outcome)) result.decision_outcome = outcome;
  const binding = stripPythonWhitespace(scalarText(item.capability_binding_ref)).toLowerCase();
  if (/^[a-z][a-z0-9_.-]{0,31}:[a-z][a-z0-9_.:-]{2,95}$/u.test(binding)) result.capability_binding_ref = binding;
  if (item.task_repository) {
    try {const repository = normalizeTodoRepository(item.task_repository); if (repository) result.task_repository = repository;}
    catch (error) {if (!(error instanceof EffectRuntimeRequestError)) throw error;}
  }
  for (const [key, values] of [["continuation_policy", ["independent_handoff", "same_agent_non_delivery"]],
    ["removed_continuation_policy", ["primary_review", "review_handoff"]]] as const) {
    const value = stripPythonWhitespace(scalarText(item[key])).toLowerCase();
    if ((values as readonly string[]).includes(value)) result[key] = value;
  }
  for (const key of ["resume_monitor_generation", "material_change_generation"]) {
    const value = item[key];
    if (value != null && value !== "" && (typeof value === "number" || typeof value === "boolean" || typeof value === "string" && /^[+-]?\d+$/.test(value.trim()))) {
      const number = Math.trunc(Number(value));
      if (Number.isSafeInteger(number) && number >= 0) result[key] = number;
    }
  }
  for (const key of ["action_kind", "task_domain"]) {
    const value = compactPythonWhitespace(text(item[key])).toLowerCase();
    if (new RegExp(key === "action_kind" ? "^[a-z][a-z0-9_-]{0,63}$" : "^[a-z][a-z0-9_.-]{0,63}$").test(value)) result[key] = value;
  }
  const noFollowup = readNoFollowup(item.no_followup);
  if (noFollowup !== null) result.no_followup = noFollowup;
  const declarationKeys = ["validation_command", "validation_command_argv", "validation_label", "validation_timeout_seconds"];
  if (declarationKeys.some(key => item[key] != null && item[key] !== "")) {
    const command = typeof item.validation_command === "string" ? stripPythonWhitespace(item.validation_command) : item.validation_command;
    let argv = item.validation_command_argv ?? null;
    if (typeof argv === "string") {
      argv = stripPythonWhitespace(argv);
      if (!argv) argv = null;
      else {try {argv = JSON.parse(argv as string);} catch (error) {if (!(error instanceof SyntaxError)) throw error;}}
    }
    const timeout = typeof item.validation_timeout_seconds === "string" && /^\d+$/.test(item.validation_timeout_seconds.trim())
      ? Number(item.validation_timeout_seconds.trim()) : item.validation_timeout_seconds === "" ? null : item.validation_timeout_seconds ?? null;
    result.completion_validation_required = true;
    result.completion_validation_sha256 = canonicalAuthoritySha256({validation_command: command === "" ? null : command ?? null,
      validation_command_argv: argv, validation_label: item.validation_label === "" ? null : item.validation_label ?? null,
      validation_timeout_seconds: timeout});
    if (!Object.hasOwn(result, "completion_validation_revision")) result.completion_validation_revision = 0;
    if (!Object.hasOwn(result, "completion_validation_revision_history")) result.completion_validation_revision_history = [];
  }
  for (const key of declarationKeys) delete result[key];
  return result;
}
