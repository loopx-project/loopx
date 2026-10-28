import {decodeTodoReadSource, handoffReadSource, handoffSource, copyTodoPythonValues, pythonFieldText,
  id, object, taskClass, readResume, readScope, readScopes, readExcludedAgents, writeScopes, compactTodoReadText} from "./read_codec.ts";
import {stripPythonWhitespace} from "../coordination/todo_agents.ts";
import {normalizeTodoRepository} from "./work_requirements.ts";
import {INTERNAL_STATE_SHAPE_PATTERNS} from "../goals/vision_checkpoint.ts";
import codec from "./read_legacy_codec.json" with {type: "json"};
import {projectTodoHandoffContext} from "./handoff_projection.ts";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireInteger, requireJsonObject } from "../runtime_decode.ts";
import { canonicalAuthorityJson } from "../coordination/authority_store_codec.ts";
import {compactThinTodoRead, compactTodoReadSummary, compactThinTodoReadSummary} from "./read_presentation.ts";
import {TODO_DECISION_SCOPE_OUTCOME_SCHEMA_VERSION} from "./user_completion_types.ts";
import {removedTodoContinuationPolicy} from "./legacy_continuation_policy_migration.ts";

// A read lens over canonical records, shared by global and Goal conversations.
// These are existing Todo facts, not dependency evaluation or execution grants.
const CONTEXT_FIELDS = [
  "todo_id", "role", "status", "priority", "task_class", "claimed_by",
  "bound_agent", "goal_bound", "blocks_agent", "global_gate", "unblocks_todo_id",
  "action_kind", "next_due_at", "expires_at", "resume_when", "resume_ready",
  "successor_todo_ids", "decision_scope", "required_decision_scopes",
] as const;

/** Select before redaction; never convert nested scopes or booleans to prose. */
export function projectTodoContextPage(input: JsonObject): JsonObject {
  if (input.summary_display !== undefined) {
    if (Object.keys(input).length !== 1) throw new EffectRuntimeRequestError("summary display cannot be combined with other views");
    return {items: projectTodoSummaryDisplay(requireJsonObject(input.summary_display, "summary display"))};
  }
  if (input.thin_summary_payload !== undefined) {
    const details = requireJsonObject(input.details, "thin summary details");
    return compactThinTodoReadSummary(requireJsonObject(input.thin_summary_payload, "thin Todo summary"), {
      role: details.role, items_matched: requireInteger(details.items_matched, "items matched"),
      items_returned: requireInteger(details.items_returned, "items returned"),
      item_limit_per_role: requireInteger(details.item_limit_per_role, "item limit per role")});
  }
  if (input.thin_payload !== undefined) {
    if (Object.keys(input).some(key => key !== "thin_payload")) throw new EffectRuntimeRequestError("thin Todo presentation cannot be combined with other views");
    return compactThinTodoRead(requireJsonObject(input.thin_payload, "thin Todo payload"));
  }
  if (input.summary_payload !== undefined) {
    if (Object.keys(input).some(key => !["summary_payload", "role", "limit"].includes(key))) throw new EffectRuntimeRequestError("Todo summary presentation cannot be combined with other views");
    return compactTodoReadSummary(requireJsonObject(input.summary_payload, "Todo summary payload"), String(input.role), input.limit == null ? null : Number(input.limit));
  }
  if (input.handoff_sources !== undefined) {
    if (Object.keys(input).some(key => key !== "handoff_sources" && key !== "handoff_followups")) {
      throw new EffectRuntimeRequestError("handoff context cannot be combined with list/detail/page inputs");
    }
    return {handoff_context: projectTodoHandoffContext(
      Array.isArray(input.handoff_sources) ? input.handoff_sources.map(row => handoffReadSource(requireJsonObject(row, "handoff source"))) : input.handoff_sources,
      Array.isArray(input.handoff_followups) ? input.handoff_followups.map(row => row === null ? null : handoffReadSource(requireJsonObject(row, "handoff followup"))) : input.handoff_followups)};
  }
  if (input.list_payload !== undefined) return projectTodoListPayload(input);
  if (input.detail_payload !== undefined) return projectTodoDetailPayload(input);
  if (!Array.isArray(input.records)) throw new EffectRuntimeRequestError("Todo context records must be an array");
  const records = input.records.map(value => requireJsonObject(value, "Todo context record"));
  const owner = requireBoolean(input.owner_scope, "owner_scope");
  const offset = requireInteger(input.offset, "offset");
  const limit = requireInteger(input.limit, "limit");
  if (offset < 0 || limit < 1 || limit > 48) throw new EffectRuntimeRequestError("Todo context page is out of range");
  const exact = input.todo_id !== undefined && input.todo_id !== null;
  if (exact && (typeof input.todo_id !== "string" || !input.todo_id)) {
    throw new EffectRuntimeRequestError("todo_id must be a non-empty string");
  }
  // Exact reads can recover a completed referent, without resurrecting work.
  const active = records.filter(row => exact ? row.todo_id === input.todo_id
    : row.status === "open" || row.status === "blocked" || row.status === "deferred");
  active.sort((a, b) => Number(a.role !== "user") - Number(b.role !== "user")
    || (String(a.priority ?? "Z") < String(b.priority ?? "Z") ? -1
      : String(a.priority ?? "Z") > String(b.priority ?? "Z") ? 1 : 0));
  const todos = active.slice(offset, offset + limit).map(record => {
    const row: JsonObject = {};
    for (const key of CONTEXT_FIELDS) {
      if (record[key] !== undefined && record[key] !== null) row[key] = record[key];
    }
    let truncated = false;
    const text = (value: unknown, cap: number): string => {
      const full = typeof value === "string" ? value : "";
      const characters = Array.from(full);
      if (exact || characters.length <= cap) return full;
      truncated = true;
      return characters.slice(0, cap - 3).join("") + "...";
    };
    // Native exact reads restore text; title may still be a derived summary.
    row.title = text(exact ? record.text || record.title : record.title || record.text, 420);
    if (owner) row.continuation = text(record.note || record.continuation_hint, 280);
    row.content_truncated = truncated;
    return row;
  });
  return {todos, coverage: {active: active.length, included: todos.length,
    omitted: Math.max(0, active.length - todos.length)}};
}

/** Lossless CLI presentation only; the full decision read model stays intact. */
function projectTodoListPayload(input: JsonObject): JsonObject {
  if (Object.keys(input).some(key => key !== "list_payload")) {
    throw new EffectRuntimeRequestError("Todo list and detail/page inputs cannot be combined");
  }
  const payload = requireJsonObject(input.list_payload, "Todo list payload");
  if (payload.ok !== true || payload.command !== "list" || !Array.isArray(payload.todos)
      || payload.todo_id_filter !== undefined || payload.thin) {
    throw new EffectRuntimeRequestError("Todo list references require an ordinary successful inventory");
  }
  const records = new Map<string, string>();
  const key = (row: JsonObject): string => JSON.stringify(canonicalAuthorityJson(row));
  // Keep selection order, counts and source records, including ambiguous ids.
  payload.todos.forEach((value, index) => {
    const row = requireJsonObject(value, "Todo list record");
    const identity = key(row);
    if (!records.has(identity)) records.set(identity, `#/todos/${index}`);
  });
  const result: JsonObject = {...payload};
  let replaced = 0;
  for (const role of ["user_todos", "agent_todos"]) {
    if (payload[role] === undefined) continue;
    const summary = requireJsonObject(payload[role], "Todo role summary");
    result[role] = Object.fromEntries(Object.entries(summary).map(([lane, values]) => {
      if (!Array.isArray(values)) return [lane, values];
      const pointer = lane.replace(/~/g, "~0").replace(/\//g, "~1");
      return [lane, values.map((value, index) => {
        if (!value || typeof value !== "object" || Array.isArray(value)
            || typeof (value as JsonObject).todo_id !== "string") return value;
        const row = value as JsonObject;
        const identity = key(row);
        const reference = records.get(identity);
        if (reference !== undefined) {
          replaced += 1;
          return {$ref: reference};
        }
        // A lane may include a record outside --limit, or distinct derived facts.
        // Keep its first complete projection; never merge by id or body text.
        records.set(identity, `#/${role}/${pointer}/${index}`);
        return row;
      })];
    }));
  }
  result.todo_list_record_references = {
    replaced_view_records: replaced,
    readback: "Resolve $ref at its JSON pointer within this response to read the identical record. "
      + "Distinct projected facts remain separate. Existing overview excerpts do not replace "
      + "an exact Todo read or fresh execution admission.",
  };
  return result;
}

/** Lossless lens over an already scoped exact CLI read; never selects work. */
function projectTodoDetailPayload(input: JsonObject): JsonObject {
  if (Object.keys(input).some(key => key !== "detail_payload")) {
    throw new EffectRuntimeRequestError("Todo detail and context page inputs cannot be combined");
  }
  const payload = requireJsonObject(input.detail_payload, "Todo detail payload");
  const id = payload.todo_id_filter;
  if (typeof id !== "string" || !id) {
    throw new EffectRuntimeRequestError("Todo detail requires an exact Todo identity");
  }
  if (!Array.isArray(payload.todos) || payload.todos.length > 1 || payload.ambiguous) {
    throw new EffectRuntimeRequestError("Todo detail refuses ambiguous source records");
  }
  const todo = payload.todos.length ? requireJsonObject(payload.todos[0], "Todo source row") : null;
  if (requireBoolean(payload.matched, "matched") !== (todo !== null)
      || (todo && (todo.todo_id !== id || typeof todo.text !== "string"))) {
    throw new EffectRuntimeRequestError("Todo detail source identity or body is inconsistent");
  }
  const omitted = ["todos", "agent_todos", "user_todos"];
  const result: JsonObject = {
    ...Object.fromEntries(Object.entries(payload).filter(([key]) => !omitted.includes(key))), todo,
  };
  result.todo_detail_projection = {
    schema_version: "todo_detail_projection_v0", body_path: "todo.text",
    source_complete: todo !== null, omitted_views: omitted,
  };
  return result;
}

const SUMMARY_FIELDS = [
  "goal_acceptance_guard", "schema_version", "todo_id", "content_revision", "role",
  "status", "priority", "title", "archive_state", "source_section",
  "task_class", "action_kind", "task_domain", "capability_binding_ref", "task_repository",
  "continuation_policy", "removed_continuation_policy", "required_write_scopes", "required_capabilities", "target_capabilities",
  "decision_scope", "required_decision_scopes", "decision_outcome", "decision_scope_outcomes", "claimed_by",
  "bound_agent", "goal_bound", "blocks_agent", "excluded_agents", "global_gate",
  "unblocks_todo_id", "resume_when", "resume_monitor_generation", "resume_condition", "resume_ready",
  "blocked_retry", "no_followup", "successor_todo_ids", "completion_continuation", "completion_recovery",
  "replan_obligation_id", "target_key", "cadence", "next_due_at", "expires_at",
  "watch_only", "last_checked_at", "result_hash", "consecutive_no_change", "material_change",
  "material_change_generation", "max_no_change_before_replan", "route_continuation_replan_required", "route_continuation_reason", "route_id",
  "route_key", "completed_at", "updated_at", "completion_turn_key", "superseded_by", "handoff_note",
];

const SUMMARY_SOURCES = [
  "active_next_action_items", "active_next_action_executable_items", "first_open_items", "backlog_items", "unclaimed_priority_open_items",
  "claimed_open_items", "claimed_advancement_open_items", "claimed_monitor_open_items", "monitor_open_items", "current_agent_claimed_open_items",
  "current_agent_claimed_advancement_items", "current_agent_claimed_monitor_items", "blocker_items", "resume_blocked_items", "monitor_blocked_resume_candidates",
  "current_agent_monitor_blocked_resume_candidates", "unclaimed_monitor_blocked_resume_candidates", "items",
];

const PLANNING_SOURCES = ["items", "deferred_items", "blocker_items", "monitor_open_items"];

function compactSummaryRecord(item: JsonObject, body: unknown): JsonObject {
  const row: JsonObject = {index: item.index ?? null, text: body};
  for (const key of SUMMARY_FIELDS) if (item[key] != null) row[key] = item[key];
  const token = (key: string) => stripPythonWhitespace(pythonFieldText(row, key));
  copyTodoPythonValues(item, row);
  let repository: string | null = null;
  try {repository = normalizeTodoRepository(token("task_repository"));}
  catch (error) {if (!(error instanceof EffectRuntimeRequestError)) throw error;}
  const outcome = (value: unknown) => ["approve", "reject", "cancel"].includes(String(value).trim().toLowerCase()) ? String(value).trim().toLowerCase() : null;
  const outcomes: JsonObject[] = [];
  const raw = row.decision_scope_outcomes;
  for (const value of Array.isArray(raw) ? raw : typeof raw === "string" ? raw.split(",") : [raw]) {
    const parts = typeof value === "string" ? value.split("|", 3) : [];
    const source = object(value), decision = outcome(source.outcome ?? parts[0]);
    const scope = readScope(source.decision_scope || source.scope || parts[1]), todo = id(source.source_todo_id ?? parts[2]);
    if (!decision || !scope || !todo) continue;
    const record = {schema_version: TODO_DECISION_SCOPE_OUTCOME_SCHEMA_VERSION, outcome: decision, decision_scope: scope, source_todo_id: todo};
    const position = outcomes.findIndex(row => {const prior = object(row.decision_scope); return prior.kind === scope.kind && prior.granularity === scope.granularity && prior.scope_key === scope.scope_key;});
    if (position < 0) outcomes.push(record); else outcomes[position] = record;
  }
  const normalized: JsonObject = {
    task_domain: /^[a-z][a-z0-9_.-]{0,63}$/.test(token("task_domain").toLowerCase()) ? token("task_domain").toLowerCase() : null,
    required_write_scopes: writeScopes(Array.isArray(row.required_write_scopes) ? row.required_write_scopes : token("required_write_scopes")), task_repository: repository,
    decision_scope: readScope(row.decision_scope), required_decision_scopes: readScopes(row.required_decision_scopes),
    decision_outcome: outcome(token("decision_outcome")), decision_scope_outcomes: outcomes,
    excluded_agents: readExcludedAgents(row.excluded_agents),
    removed_continuation_policy: removedTodoContinuationPolicy(token("removed_continuation_policy")),
    replan_obligation_id: /^replan-[a-f0-9]{16}$/.test(token("replan_obligation_id")) ? token("replan_obligation_id") : null};
  for (const [key, value] of Object.entries(normalized)) {
    if (value !== null && (!Array.isArray(value) || value.length)) row[key] = value; else delete row[key];
  }
  const classify = (item: JsonObject) => taskClass({...item, text: ["title", "text"].map(key => pythonFieldText(item, key)).join(" ")});
  row.task_class = classify(row);
  if (classify(item) === "blocker" || stripPythonWhitespace(pythonFieldText(item, "status")).toLowerCase() === "blocked") {
    const reason = compactTodoReadText(pythonFieldText(item, "reason"), 220);
    if (reason && !INTERNAL_STATE_SHAPE_PATTERNS.some(rule => rule.test(reason))) row.reason = reason;
  }
  return row;
}

/** Standalone compatibility display, also composed inside quota planning. */
export function projectTodoSummaryDisplay(input: JsonObject): JsonObject[] {
  const planning = input.include_terminal !== undefined, terminal = input.include_terminal === true;
  const source = input.source === undefined ? null : decodeTodoReadSource(requireJsonObject(input.source, "summary source"));
  const ready = new Set((Array.isArray(input.handoff_gates) ? input.handoff_gates : []).flatMap(value => {
    const gate = object(value);
    return gate.gate_state === "cleared_with_successor" && Array.isArray(gate.successor_todo_ids) ? gate.successor_todo_ids.map(id).filter(Boolean) : [];
  }));
  const selected: {item: JsonObject; text: unknown}[] = [], seen = new Set<string>();
  if (source) {
    for (const key of planning ? PLANNING_SOURCES : SUMMARY_SOURCES) {
      for (const value of Array.isArray(source[key]) ? source[key] as unknown[] : []) {
        if (!value || typeof value !== "object" || Array.isArray(value)) continue;
        const item = value as JsonObject, body = stripPythonWhitespace(pythonFieldText(item, "text"));
        let todo = id(item.todo_id);
        if (terminal && !todo) {const legacy = stripPythonWhitespace(pythonFieldText(item, "todo_id")); if (legacy.startsWith("todo_")) todo = legacy;}
        if (!body || (planning ? !todo || !terminal && item.status === "done" || seen.has(todo!) : item.done === true)) continue;
        if (!planning && selected.some(({item: prior, text}) => item.todo_id && prior.todo_id ? item.todo_id === prior.todo_id : prior.index === item.index && text === body)) continue;
        if (todo) seen.add(todo);
        selected.push({item, text: body});
      }
    }
  } else {
    if (!Array.isArray(input.items)) throw new EffectRuntimeRequestError("summary items must be an array");
    for (const value of input.items) {
      const encoded = requireJsonObject(value, "summary item"), item = decodeTodoReadSource(encoded);
      selected.push({item, text: encoded.text ?? (input.strip_text && !Object.hasOwn(encoded, "text") ? stripPythonWhitespace(pythonFieldText(item, "text")) : item.text ?? null)});
    }
  }
  return selected.map(({item, text}) => {
    const row = compactSummaryRecord(item, text);
    const hint = projectTodoHandoffContext([handoffSource(item)])[0]!.continuation_hint;
    const note = projectTodoHandoffContext([handoffSource(row)])[0]!.note;
    if (row.task_class === "advancement_task" && row.status === "open" && hint) row.continuation_hint = hint;
    if (note) row.handoff_note = note;
    if (source && !planning && ready.has(id(row.todo_id)) && readResume(row.resume_when) && !Object.hasOwn(row, "resume_ready")) {
      row.resume_ready = true;
      row.resume_condition = {schema_version: "todo_resume_condition_v0", resume_when: row.resume_when, satisfied: true, source: "handoff_gate_cleared_with_successor"};
    }
    if (terminal) for (const key of [...codec.frontier_fields, "evidence", "note", "last_actor_agent_id"]) if (item[key] != null) row[key] = item[key];
    return row;
  });
}
