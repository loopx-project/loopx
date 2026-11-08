/** One full-source Todo read reduction; the existing owners decide every lane. */
import {evaluateTodoCompletionFence, TODO_COMPLETION_FENCE_REQUEST_SCHEMA} from "./completion_fence.ts";
import {normalizeTodoNoFollowup, normalizeTodoCompletionContinuation, normalizeTodoCompletionRecovery} from "./completion_state.ts";
import {pythonFieldText} from "./read_codec.ts";
import {authorityUnicodeCompare} from "../coordination/authority_store_codec.ts";
import {stripPythonWhitespace} from "../coordination/todo_agents.ts";
import {compactTodoReadSummary} from "./read_presentation.ts";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireBoolean} from "../runtime_decode.ts";
import {schedulerTimestampMilliseconds} from "../scheduler/state_store.ts";
import {evaluateTodoResumeConditionsFromSource} from "./resume_condition.ts";
import {readTodoPriority, todoPriorityRank} from "./priority.ts";
import {evaluateTodoSuccession, validateTodoSuccession, SUCCESSION_CONTEXT_FIELDS} from "./succession.ts";
import {projectTodoSummary} from "./summary_projection.ts";
import {projectTodoHandoffContext} from "./handoff_projection.ts";
import {projectStandingDecisions} from "./standing_decision.ts";
import {configuredGoalChecks} from "../goals/acceptance_contract.ts";
import codec from "./read_legacy_codec.json" with {type: "json"};

export const TODO_READ_REQUEST = "todo_read_request_v0";
export const TODO_READ_RESULT = "todo_read_result_v0";
import {recordFields, id, object, text, ids, withoutNull, compactTodoReadText, pythonJson,
  taskClass, canonicalTodoReadItems, structureCanonicalTodo, readAgent, readExcludedAgents, readNoFollowup, readResume, handoffSource} from "./read_codec.ts";

function successionFacts(item: JsonObject): JsonObject {
  const resume = readResume(item.resume_when) || "", handoff = readExcludedAgents(item.excluded_agents).length > 0 && id(item.unblocks_todo_id) !== null;
  const status = stripPythonWhitespace(text(item.status)).toLowerCase();
  return {todo_id: id(item.todo_id), status: ["open", "done", "blocked", "deferred"].includes(status) ? status : item.done ? "done" : "open",
    active: (item.archive_state || "active") === "active", advancement: taskClass(item) === "advancement_task",
    no_followup: readNoFollowup(item.no_followup) === true, successors: ids(item.successor_todo_ids),
    superseded_by: id(item.superseded_by), unblocks: id(item.unblocks_todo_id),
    resumes: resume.startsWith("todo_done:") ? id(resume.slice(10)) : null, handoff, done: item.done === true,
    route_flag: typeof item.route_continuation_replan_required === "boolean" ? item.route_continuation_replan_required : null,
    legacy_route_label: handoff && typeof item.route_continuation_replan_required !== "boolean"
      ? [item.action_kind, item.title, item.text].filter(v => text(v).trim()).map(text).join(" ") : "",
    context_fields: SUCCESSION_CONTEXT_FIELDS.filter(key => Object.prototype.propertyIsEnumerable.call(item, key) && item[key] != null)};
}
function compactRecord(item: JsonObject): JsonObject {
  const result: JsonObject = {index: item.index ?? null, done: Boolean(item.done), text: item.text ?? null};
  for (const key of recordFields) if (!Object.hasOwn(result, key) && item[key] != null) result[key] = item[key]!;
  if (item.content_revision) result.content_revision = item.content_revision;
  if (item.goal_acceptance_guard && typeof item.goal_acceptance_guard === "object") result.goal_acceptance_guard = item.goal_acceptance_guard;
  return result;
}

function summaryRow(item: JsonObject, evaluation: JsonObject, preferred: Set<unknown>): JsonObject {
  const resume = readResume(item.resume_when) || "", status = item.status || (item.done ? "done" : "open");
  const index = typeof item.index === "number" && Number.isInteger(item.index) && item.index >= 0 ? item.index : 999999;
  return {status, done: Boolean(item.done), task_class: taskClass(item), has_resume: Boolean(resume), resume_ready: item.resume_ready ?? null,
    resume_evaluated: resumeEvaluated(item),
    acceptance_blocked: object(item.goal_acceptance_guard).allowed === false, claimed: Boolean(item.claimed_by),
    preferred: preferred.has(item.todo_id), watch_only: taskClass(item) === "continuous_monitor" && (item.watch_only === true || stripPythonWhitespace(text(item.watch_only || "")).toLowerCase() === "true"),
    due_at: readScheduleTime(item.next_due_at), expires_at: readScheduleTime(item.expires_at),
    sort: [todoPriorityRank(readTodoPriority(item)), index,
      index === 999999 ? text(item.completed_at || item.updated_at) : "", index === 999999 ? text(item.todo_id) : ""],
    completed_at: item.completed_at || null, updated_at: item.updated_at || null, completion_index: Number(item.index || 0),
    linked_user_action: id(item.unblocks_todo_id) !== null, no_followup: readNoFollowup(item.no_followup) === true,
    successor_gap: evaluation.successor_gap!, handoff_state: evaluation.handoff_state!, replan: evaluation.route_continuation_replan_required!,
    todo_id: id(item.todo_id), claim: readAgent(item.claimed_by), bound: readAgent(item.bound_agent), blocks: readAgent(item.blocks_agent),
    global: Boolean(item.global_gate), excluded: readExcludedAgents(item.excluded_agents)};
}

function resumeEvaluated(item: JsonObject): boolean {
  const resume = readResume(item.resume_when), condition = object(item.resume_condition);
  return !resume || condition.schema_version === "todo_resume_condition_v0" && condition.resume_when === resume &&
    typeof condition.satisfied === "boolean" && item.resume_ready === condition.satisfied;
}

function readScheduleTime(value: unknown): number | null {
  if (typeof value === "string") {
    const date = /^([0-9]{4})-([0-9]{2})-([0-9]{2})/.exec(value.trim());
    if (date) {
      const year = Number(date[1]), month = Number(date[2]), day = Number(date[3]);
      const check = new Date(0); check.setUTCFullYear(year, month - 1, day);
      if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) return null;
    }
  }
  const milliseconds = schedulerTimestampMilliseconds(value);
  return milliseconds === null ? null : milliseconds / 1000;
}

function frontierFacts(items: JsonObject[]): JsonObject[] {
  return items.map(item => ({id: stripPythonWhitespace(text(item.todo_id)), claim: readAgent(item.claimed_by),
    excluded: readExcludedAgents(item.excluded_agents), updated: stripPythonWhitespace(text(item.updated_at || item.completed_at)),
    advancement: taskClass(item) === "advancement_task", actionable: item.done !== true && item.status === "open" && (!readResume(item.resume_when) || item.resume_ready === true) && object(item.goal_acceptance_guard).allowed !== false,
    serialized: pythonJson(Object.fromEntries(codec.frontier_fields.filter(key => item[key] != null).map(key => [key, item[key]!])))}));
}

function handoffGates(items: JsonObject[], evaluations: JsonObject[]): JsonObject[] {
  const fields = ["todo_id", "role", "task_class", "action_kind", "task_domain", "task_repository", "continuation_policy", "claimed_by", "unblocks_todo_id", "resume_when", "no_followup", "superseded_by", "route_continuation_replan_required", "route_continuation_reason", "route_id", "route_key", "next_due_at"];
  return items.flatMap((item, i) => {
    const decision = evaluations[i]!;
    if (decision.handoff_state == null) return [];
    const successors = decision.successor_todo_ids as string[];
    return [withoutNull({schema_version: "todo_handoff_gate_v1", gate_state: decision.handoff_state,
      index: item.index ?? null, done: item.done === true || item.status === "done" || item.status === "deferred", status: item.status!,
      text: stripPythonWhitespace(text(item.text)), excluded_agents: item.excluded_agents ?? [], successor_count: successors.length,
      ...Object.fromEntries(fields.filter(key => item[key] != null).map(key => [key, item[key]!])),
      ...(decision.route_continuation_replan_required ? {route_continuation_replan_required: true,
        route_continuation_reason: item.route_continuation_reason ?? "stale handoff closeout requires a bounded route continuation replan"} : {}),
      ...(successors.length ? {successor_todo_ids: successors} : {})})];
  }).sort((a, b) => Number(a.index || 999999) - Number(b.index || 999999) || authorityUnicodeCompare(text(a.todo_id), text(b.todo_id)));
}

const CAPABILITY_ACTION_ITEM_LIMIT = 3;
const CAPABILITY_ACTION_BINDING = /^[a-z][a-z0-9_.-]{0,31}:[a-z][a-z0-9_.:-]{2,95}$/u;
const CAPABILITY_ACTION_KIND = /^[a-z][a-z0-9_-]{0,63}$/;

/** Capability-bound User actions grouped before the generic Todo display cap. */
function projectCapabilityActions(
  items: readonly JsonObject[], openIndices: readonly number[], compacts: readonly JsonObject[],
): JsonObject {
  const groups = new Map<string, number[]>();
  for (const index of openIndices) {
    const item = items[index]!;
    const status = stripPythonWhitespace(text(item.status || (item.done === true ? "done" : "open"))).toLowerCase();
    if (item.done === true || item.archive_state === "archive" || id(item.superseded_by) !== null
        || !["open", "blocked"].includes(status)) continue;
    if (taskClass(item) !== "user_action") continue;
    const actionKind = stripPythonWhitespace(text(item.action_kind)).toLowerCase();
    const binding = stripPythonWhitespace(text(item.capability_binding_ref)).toLowerCase();
    if (!CAPABILITY_ACTION_KIND.test(actionKind) || !CAPABILITY_ACTION_BINDING.test(binding)) continue;
    const group = groups.get(actionKind) ?? [];
    group.push(index);
    groups.set(actionKind, group);
  }
  const byRecency = (left: number, right: number) => {
    const l = compacts[left]!, r = compacts[right]!;
    return authorityUnicodeCompare(text(r.updated_at || r.created_at), text(l.updated_at || l.created_at)) ||
      authorityUnicodeCompare(text(l.todo_id), text(r.todo_id));
  };
  const projectedGroups = [...groups.entries()].map(([actionKind, indices]) => {
    const ordered = [...indices].sort(byRecency);
    return {
      action_kind: actionKind,
      count: ordered.length,
      latest_at: ordered.length ? (compacts[ordered[0]!]!.updated_at ?? compacts[ordered[0]!]!.created_at ?? null) : null,
      items: ordered.slice(0, CAPABILITY_ACTION_ITEM_LIMIT).flatMap(index => {
        const item = items[index]!;
        const summary = compacts[index]!;
        const todoId = id(item.todo_id);
        const title = compactTodoReadText(text(item.title || item.text), 200);
        if (!todoId || !title) return [];
        return [withoutNull({
          index: item.index ?? null,
          done: false,
          text: title,
          todo_id: todoId,
          role: "user",
          goal_id: item.goal_id ?? null,
          status: text(item.status) || "open",
          task_class: "user_action",
          action_kind: actionKind,
          capability_binding_ref: text(item.capability_binding_ref).toLowerCase(),
          created_by: summary.created_by ?? null,
          bound_agent: summary.bound_agent ?? null,
          created_at: summary.created_at ?? null,
          updated_at: summary.updated_at ?? null,
        })];
      }),
    };
  }).sort((left, right) =>
    authorityUnicodeCompare(text(right.latest_at), text(left.latest_at)) ||
    authorityUnicodeCompare(left.action_kind, right.action_kind));
  return {
    schema_version: "todo_capability_actions_v0",
    total_count: projectedGroups.reduce((sum, group) => sum + group.count, 0),
    groups: projectedGroups,
  };
}

export async function projectTodoReadGroup(input: JsonObject): Promise<JsonObject> {
  if (!Array.isArray(input.items) || !Array.isArray(input.lineage)) throw new EffectRuntimeRequestError("Todo read requires complete source arrays");
  const items = input.items.map(value => ({...requireJsonObject(value, "Todo read item")}));
  const lineage = input.lineage.map(value => requireJsonObject(value, "Todo read lineage"));
  const lineageFacts = input.lineage_facts == null ? lineage.map(successionFacts) : input.lineage_facts;
  if (!Array.isArray(lineageFacts) || lineageFacts.length !== lineage.length) throw new EffectRuntimeRequestError("Todo lineage fact cardinality mismatch");
  for (const [index, value] of lineageFacts.entries()) {
    const fact = requireJsonObject(value, "Todo lineage facts");
    if (fact.todo_id !== id(lineage[index]!.todo_id) || fact.active !== ((lineage[index]!.archive_state || "active") === "active")) {
      throw new EffectRuntimeRequestError("Todo lineage facts disagree with source identity");
    }
  }
  const evaluated = requireBoolean(input.evaluated, "evaluated");
  if (evaluated && items.some(item => !resumeEvaluated(item))) {
    throw new EffectRuntimeRequestError("Todo display requires a matching full-source resume evaluation");
  }
  if (!evaluated) {
    const waiting = items.filter(item => readResume(item.resume_when));
    if (waiting.length) {
      const result = await evaluateTodoResumeConditionsFromSource({schema_version: "todo_resume_evaluation_request_v0", items: waiting,
        source_items: [...(Array.isArray(input.resume_lineage) ? input.resume_lineage : lineage), ...items], rollout_events: input.rollout_events ?? [], evaluated_at: input.evaluated_at!,
        ...(input.rollout_event_source == null ? {} : {rollout_event_source: input.rollout_event_source}),
        available_capabilities: input.available_capabilities ?? null});
      const conditions = new Map((result.conditions as JsonObject[]).map(row => [row.todo_id, row.condition]));
      for (const item of waiting) {
        item.resume_condition = conditions.get(item.todo_id) ?? {schema_version: "todo_resume_condition_v0", resume_when: item.resume_when!, satisfied: false, unsupported: true};
        item.resume_ready = object(item.resume_condition).satisfied === true;
      }
    }
  }
  const facts = items.map(successionFacts);
  let decisions: JsonObject[];
  if (items.length === 0) {
    decisions = [];
  } else if (evaluated) {
    if (!Array.isArray(input.evaluations) || input.evaluations.length !== items.length || input.evaluations.some(value => value == null)) throw new EffectRuntimeRequestError("Todo display requires a matching full-source succession evaluation");
    const evaluations = input.evaluations;
    decisions = facts.map((row, i) => validateTodoSuccession(row, evaluations[i]));
  } else {
    const selected = new Map<string, number>();
    const key = (row: JsonObject) => JSON.stringify([id(row.todo_id), row.role ?? null, row.archive_state || "active"]);
    for (const item of items) selected.set(key(item), (selected.get(key(item)) ?? 0) + 1);
    const source: JsonObject[] = [];
    lineage.forEach((row, index) => {
      const count = selected.get(key(row)) ?? 0;
      if (!count) source.push(requireJsonObject(lineageFacts[index], "Todo lineage facts"));
      else selected.set(key(row), count - 1);
    });
    try {
      decisions = evaluateTodoSuccession([...source, ...facts], source.length);
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      return {schema_version: TODO_READ_RESULT, rejection: {phase: "succession", message: error.message}};
    }
  }
  const preferred = new Set(Array.isArray(input.preferred_todo_ids) ? input.preferred_todo_ids : []);
  const rows = items.map((item, i) => summaryRow(item, decisions[i]!, preferred));
  const compacts = items.map(compactRecord);
  const projected = projectTodoSummary({rows, succession: {facts, evaluations: decisions},
    frontier_rows: input.role === "agent" ? frontierFacts(items) : null,
    observed_at: input.observed_at!, selection: input.selection ?? null, role: input.role ?? null,
    source_section: input.source_section ?? null, item_limit: input.item_limit ?? null, full_selection: input.full_selection ?? true});
  const visible = [...new Set([...Object.values(projected.lanes).flatMap(lane => lane.format === "raw" ? [] : lane.indices),
    ...Object.values(projected.orchestration).flat()])].sort((a, b) => a - b);
  const handoffs = projectTodoHandoffContext(visible.map(index => handoffSource(compacts[index]!)));
  visible.forEach((index, i) => {if (handoffs[i]!.note) compacts[index]!.handoff_note = handoffs[i]!.note;});
  const summary = projected.fields;
  for (const [name, lane] of Object.entries(projected.lanes)) {
    summary[name] = lane.indices.map(index => {
      if (lane.format === "raw") return items[index]!;
      const row = {...compacts[index]!};
      const removed = lane.format === "active" ? ["note", "evidence", "reason", "completed_at", "updated_at", "superseded_by"] :
        lane.format === "recent" || lane.format === "gap" ? ["note", "evidence", "reason"] : [];
      for (const key of removed) delete row[key];
      if (lane.format === "gap") Object.assign(row, {succession_tracked: true, recommended_action: object(summary.todo_succession_warning).recommended_action});
      return row;
    });
  }
  if (input.role === "user") {
    summary.capability_actions = projectCapabilityActions(items, projected.source_indices, compacts);
  }
  if (summary.todo_succession_warning) object(summary.todo_succession_warning).items = summary.completed_without_successor_items!;
  const chosen = projected.source_indices.map(index => items[index]!);
  const chosenDecisions = projected.source_indices.map(index => decisions[index]!);
  const gates = handoffGates(chosen, chosenDecisions);
  if (gates.length) summary.handoff_gates = gates;
  return {schema_version: TODO_READ_RESULT, summary, items: chosen, evaluations: chosenDecisions,
    visible_evaluations: projected.lanes.items!.indices.map(index => decisions[index]!),
    orchestration: Object.fromEntries(Object.entries(projected.orchestration).map(([name, ordinals]) => [name, ordinals.map(i => compacts[i]!)]))};
}

export async function projectCanonicalTodoFields(input: JsonObject): Promise<JsonObject> {
  if (!Array.isArray(input.todos)) throw new EffectRuntimeRequestError("canonical Todo read source must be an array");
  const original = input.todos.map(value => requireJsonObject(value, "canonical Todo source"));
  const decisions = original.filter(row => row.role === "user");
  const standing = decisions.length ? projectStandingDecisions(decisions, false) : null;
  const all = input.presentation_ready === true ? original : canonicalTodoReadItems(original);
  const archived = new Set(all.filter(row => row.archive_state === "archive").map(row => row.todo_id));
  const acceptance = object(input.goal_acceptance_contract), guards = object(input.goal_acceptance_work_guards);
  const checksConfigured = configuredGoalChecks(acceptance) !== null;
  const lineage = Array.isArray(input.lineage) ? input.lineage.map(value => requireJsonObject(value, "Todo lineage")) : all;
  const lineageFacts = input.lineage_facts ?? lineage.map(successionFacts);
  const includeArchived = input.include_archived === true || input.retained === true;
  const query = input.kind === "query" && input.selection == null ? object(input.query) : {};
  const detailId = query.agent_id == null && query.limit == null ? query.todo_id : null;
  const fields: JsonObject = {}, evaluations: JsonObject = {};
  for (const role of ["user", "agent"]) {
    const section = role === "agent" ? "Agent Todo" : "User Todo / Owner Review Reading Queue";
    const items = all.filter(row => (row.role === "user" ? "user" : "agent") === role && (includeArchived || !archived.has(row.todo_id)))
      .map(row => structureCanonicalTodo({...row,
        ...(input.retained !== true && checksConfigured && guards[text(row.todo_id)] ? {goal_acceptance_guard: guards[text(row.todo_id)]} : {})}, role, section,
        input.retained === true ? null : 500, input.retained !== true));
    const grouped = await projectTodoReadGroup({...input, items, lineage, lineage_facts: lineageFacts, evaluated: false, role, source_section: section,
      selection: typeof detailId === "string" && detailId ? {role, todo_id: detailId, status: null, agent_id: null} : input.selection ?? null,
      item_limit: null, full_selection: true, resume_lineage: items.some(row => readResume(row.resume_when))
        ? all.map(row => structureCanonicalTodo(row, row.role ?? null, row.source_section ?? section)) : []});
    if (grouped.rejection) return grouped;
    const summary = requireJsonObject(grouped.summary, "canonical Todo summary");
    if (role === "agent" && input.retained !== true) {
      if (checksConfigured || Object.keys(object(acceptance.outcome_requirements)).length || acceptance.goal_objective) summary.goal_acceptance_contract = acceptance;
      const done = all.filter(row => archived.has(row.todo_id) && row.done === true && taskClass(row) === "advancement_task").length;
      if (done) {summary.archived_advancement_done_count = done; summary.advancement_done_count = Number(summary.advancement_done_count || 0) + done;}
    }
    fields[`${role}_todos`] = summary;
    evaluations[`${role}_todos`] = grouped.visible_evaluations;
  }
  if (standing && input.retained !== true) fields.standing_decision_authority = standing;
  return {schema_version: TODO_READ_RESULT, fields, field_evaluations: evaluations};
}

function emptySummary(role: string): JsonObject {
  return {schema_version: "todo_summary_v0", role, source_section: role === "agent" ? "Agent Todo" : "User Todo / Owner Review Reading Queue",
    total_count: 0, open_count: 0, done_count: 0, items: [], first_open_items: []};
}

export async function selectTodoReadFields(input: JsonObject): Promise<JsonObject> {
  const fields = requireJsonObject(input.fields, "Todo source fields"), evaluations = requireJsonObject(input.field_evaluations, "Todo source evaluations");
  if (!Array.isArray(input.roles) || input.roles.some(role => role !== "user" && role !== "agent")) throw new EffectRuntimeRequestError("Todo read roles must be user or agent");
  const summaries: JsonObject = {}, selectedEvaluations: JsonObject = {}, todos: unknown[] = [];
  let unfiltered = 0, uncapped = 0;
  for (const role of input.roles as string[]) {
    const key = `${role}_todos`, raw = object(fields[key]), items = Array.isArray(raw.items) ? raw.items : [];
    unfiltered += items.length;
    let summary = emptySummary(role);
    let visibleEvaluations: unknown[] = [];
    if (items.length) {
      const projected = await projectTodoReadGroup({...input, items, lineage: [], lineage_facts: [], role,
        source_section: raw.source_section || summary.source_section, evaluated: true,
        evaluations: evaluations[key], item_limit: input.limit ?? null, full_selection: true,
        selection: {role, status: input.status ?? null, todo_id: input.todo_id ?? null, agent_id: input.agent_id ?? null}});
      if (projected.rejection) return projected;
      if ((projected.items as unknown[]).length) {
        summary = requireJsonObject(projected.summary, "selected Todo summary");
        visibleEvaluations = projected.visible_evaluations as unknown[];
      }
    }
    if (raw.goal_acceptance_contract && typeof raw.goal_acceptance_contract === "object") {
      summary.goal_acceptance_contract = raw.goal_acceptance_contract;
    }
    if (input.limit != null) summary = compactTodoReadSummary(summary, role, Number(input.limit));
    summaries[key] = summary;
    selectedEvaluations[key] = visibleEvaluations.slice(0, (summary.items as unknown[]).length);
    todos.push(...summary.items as unknown[]);
    uncapped += Number(summary.total_count || 0);
  }
  return {schema_version: TODO_READ_RESULT, projection: {source: input.source_name!, summaries, todos,
    unfiltered_count: unfiltered, uncapped_todo_count: uncapped},
    ...(input.include_source_evaluations === true ? {field_evaluations: selectedEvaluations} : {})};
}

export async function projectSourceTodoFields(input: JsonObject): Promise<JsonObject> {
  if (!Array.isArray(input.groups)) throw new EffectRuntimeRequestError("Todo source requires role groups");
  let groups = input.groups;
  const query = input.projection_query == null ? null : requireJsonObject(input.projection_query, "Todo projection query");
  if (query?.exact_archive === true) {
    const target = (input.lineage as JsonObject[]).find(row => id(row.todo_id) === query.todo_id && row.archive_state === "archive");
    if (!target || !(query.roles as unknown[]).includes(target.role)) return {schema_version: TODO_READ_RESULT, projection: null};
    const group = groups.map(value => requireJsonObject(value, "Todo archive group")).find(row => row.role === target.role)!;
    const item = (group.items as JsonObject[]).find(row => id(row.todo_id) === query.todo_id)!;
    groups = [{...group, items: [{...item, source_section: String(target.source_section || "Completed Work Archive")}],
      source_section: String(target.source_section || "Completed Work Archive"), include_empty_source: true}];
  }
  const fields: JsonObject = {}, evaluations: JsonObject = {}, sources: JsonObject = {}, sourceEvaluations: JsonObject = {}, orchestration: JsonObject = {};
  for (const value of groups) {
    const group = requireJsonObject(value, "Todo source role group");
    const projected = await projectTodoReadGroup({...input, ...group, evaluated: false});
    if (projected.rejection) return projected;
    const items = projected.items as unknown[];
    if (!items.length && group.include_empty_source !== true) continue;
    fields[`${String(group.role)}_todos`] = projected.summary;
    evaluations[`${String(group.role)}_todos`] = projected.visible_evaluations;
    if (input.include_full_source === true) {
      sources[String(group.role)] = projected.items;
      sourceEvaluations[String(group.role)] = projected.evaluations;
    }
    if (input.include_task_orchestration_authority === true) orchestration[String(group.role)] = projected.orchestration;
  }
  const agent = object(fields.agent_todos);
  if (Object.keys(agent).length && Array.isArray(input.archived_items)) {
    const count = input.archived_items.filter(value => {
      const row = requireJsonObject(value, "archived Todo source");
      return row.done === true && taskClass({...row, text: compactTodoReadText(text(row.text))}) === "advancement_task";
    }).length;
    if (count) {agent.archived_advancement_done_count = count; agent.advancement_done_count = Number(agent.advancement_done_count || 0) + count;}
  }
  if (Array.isArray(input.standing_items) && input.standing_items.length) {
    const standing = projectStandingDecisions(input.standing_items.map(value => requireJsonObject(value, "standing decision source")), input.legacy_source_order === true);
    if (standing) fields.standing_decision_authority = standing;
  }
  const result = {schema_version: TODO_READ_RESULT, fields, field_evaluations: evaluations,
    ...(input.include_full_source === true ? {source_items: sources, source_evaluations: sourceEvaluations} : {}),
    ...(input.include_task_orchestration_authority === true ? {source_orchestration: orchestration} : {})};
  return query === null ? result : selectTodoReadFields({...input, ...query, fields, field_evaluations: evaluations});
}

export async function readTodo(value: unknown, observedAt = new Date().toISOString()): Promise<JsonObject> {
  const input = requireJsonObject(value, "Todo read request");
  if (input.schema_version !== TODO_READ_REQUEST || !["group", "fields", "canonical_fields", "select", "source_fields", "query", "historical_fields", "completion_evidence", "quota_projection"].includes(text(input.kind))) {
    throw new EffectRuntimeRequestError("Todo read request schema mismatch");
  }
  if (input.kind === "quota_projection") {
    const {readCanonicalQuotaProjection} = await import("./read_transaction.ts");
    return readCanonicalQuotaProjection(input);
  }
  if (input.kind === "completion_evidence") {
    const {readCompletionEvidence} = await import("./read_transaction.ts");
    return readCompletionEvidence(input);
  }
  if (input.kind === "canonical_fields" || input.kind === "query") {
    const {readCanonicalTodoProjection} = await import("./read_transaction.ts");
    return readCanonicalTodoProjection({...input, read_observed_at: observedAt});
  }
  if (input.kind === "historical_fields") {
    const {readHistoricalTodoProjection} = await import("./read_transaction.ts");
    return readHistoricalTodoProjection(input);
  }
  if (input.kind === "select") return selectTodoReadFields(input);
  if (input.kind === "source_fields") return projectSourceTodoFields(input);
  return input.kind === "fields" ? projectCanonicalTodoFields(input) : projectTodoReadGroup(input);
}

/** Complete continuation read, including the original fence-first diagnostics. */
export function projectCompletionEvidence(input: JsonObject): JsonObject {
  const todo = requireJsonObject(input.todo, "durable completion Todo");
  const operation = input.operation;
  if (!["intent", "outcome", "terminal"].includes(String(operation))) throw new EffectRuntimeRequestError("unsupported completion evidence operation");
  const terminal = operation === "terminal";
  if (terminal || input.completion_turn_key != null) {
    const fenceTodo = input.fence_todo == null ? {
      status: pythonFieldText(todo, "status") || "open",
      no_followup: typeof todo.no_followup === "boolean" ? todo.no_followup : pythonFieldText(todo, "no_followup") || null,
      completion_continuation: pythonFieldText(todo, "completion_continuation") || null,
      completion_turn_key: pythonFieldText(todo, "completion_turn_key") || null,
      successor_todo_ids: todo.successor_todo_ids ?? null,
    } : requireJsonObject(input.fence_todo, "completion fence Todo");
    const fence = evaluateTodoCompletionFence({schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
      projection_source: input.projection_source ?? "materialized", todo: fenceTodo,
      requested_no_followup: terminal, requested_completion_turn_key: input.completion_turn_key ?? null,
      requested_completion_identity_source: null, goal_id: null, todo_id: null});
    if (terminal && fence.outcome === "continue") {
      if (fence.reason === "same_turn_terminal_upgrade" || fence.reason === "not_terminal" && fence.status === "open") return {kind: "absent"};
      throw new EffectRuntimeRequestError("durable terminal completion is not safely replayable");
    }
    if (terminal && stripPythonWhitespace(pythonFieldText(todo, "status")).toLowerCase() !== "done")
      throw new EffectRuntimeRequestError("durable terminal completion is not safely replayable");
  }
  const expected = id(input.expected_todo_id);
  if (!expected) throw new EffectRuntimeRequestError("durable completion requires a public Todo id");
  if (id(pythonFieldText(todo, "todo_id")) !== expected)
    throw new EffectRuntimeRequestError("durable completion todo_id does not match the selected Todo");
  const requireDone = operation !== "intent";
  if (requireDone && stripPythonWhitespace(pythonFieldText(todo, "status")).toLowerCase() !== "done")
    throw new EffectRuntimeRequestError("durable completion requires the selected Todo to be durably done");
  const rawSuccessors = todo.successor_todo_ids;
  const successorValues = Array.isArray(rawSuccessors) ? rawSuccessors.map((_, i) => pythonFieldText(rawSuccessors, i)) : [pythonFieldText(todo, "successor_todo_ids")];
  const successors = ids(successorValues);
  const noFollowup = normalizeTodoNoFollowup(typeof todo.no_followup === "boolean" ? todo.no_followup : pythonFieldText(todo, "no_followup"));
  let continuation = normalizeTodoCompletionContinuation(pythonFieldText(todo, "completion_continuation"));
  const rawRecovery = pythonFieldText(todo, "completion_recovery"), recovery = normalizeTodoCompletionRecovery(rawRecovery);
  if (rawRecovery && recovery === null) throw new EffectRuntimeRequestError("durable completion_recovery is invalid");
  if (requireDone && continuation === null) throw new EffectRuntimeRequestError("durable completion is missing completion_continuation; repair the Todo with `loopx todo complete`");
  continuation ??= noFollowup === true ? "no_followup" : successors.length ? "successor" : "active_goal";
  let completion: JsonObject;
  if (noFollowup === true) {
    if (successors.length) throw new EffectRuntimeRequestError("durable completion records both no_followup and successor_todo_ids");
    if (continuation !== "no_followup") throw new EffectRuntimeRequestError("durable completion_continuation contradicts no_followup");
    completion = {todo_id: expected, continuation: "no_followup"};
  } else if (successors.length) {
    if (recovery !== null) throw new EffectRuntimeRequestError("durable completion_recovery requires a no_followup continuation");
    if (continuation !== "successor") throw new EffectRuntimeRequestError("durable completion_continuation contradicts successor_todo_ids");
    if (input.existing_todo_ids != null) {
      if (!Array.isArray(input.existing_todo_ids)) throw new EffectRuntimeRequestError("existing Todo ids must be an array");
      const existing = new Set(input.existing_todo_ids), missing = successors.filter(value => !existing.has(value));
      if (missing.length) throw new EffectRuntimeRequestError("durable completion declares missing successor Todo ids: " + missing.join(", "));
    }
    completion = {todo_id: expected, continuation: "successor", successor_todo_ids: successors};
  } else {
    if (continuation !== "active_goal") throw new EffectRuntimeRequestError("durable completion_continuation requires matching no_followup or successor_todo_ids");
    if (recovery !== null) throw new EffectRuntimeRequestError("durable completion_recovery requires a no_followup continuation");
    completion = {todo_id: expected, continuation: "active_goal"};
  }
  if (!terminal) return completion;
  if (completion.continuation !== "no_followup") throw new EffectRuntimeRequestError("durable terminal completion is not no-follow-up");
  return {kind: "committed", completion};
}
