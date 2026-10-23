/** Canonical provider IO and full-source summaries share one read transaction. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {readCanonicalSnapshot} from "../coordination/canonical_snapshot_page.ts";
import {readLocalCoordinationTodoSource} from "../coordination/local_authority_read.ts";
import {LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA} from "../coordination/coordination_state_contract.generated.ts";
import {projectCanonicalTodoFields, selectTodoReadFields, projectTodoReadGroup, TODO_READ_RESULT} from "./read_projection.ts";
import {canonicalTodoReadItems, structureCanonicalTodo} from "./read_codec.ts";
import {compactTodoReadSummary, compactThinTodoRead, todoReadProjectionContract} from "./read_presentation.ts";
import {projectTodoContextPage} from "./context_projection.ts";
import {sealProjectionEnvelope, PROJECTION_ENVELOPE_SEAL_REQUEST} from "../projection_envelope.ts";

export async function readCanonicalTodoProjection(input: JsonObject): Promise<JsonObject> {
  const source = requireJsonObject(input.source, "canonical Todo read source");
  const todos: unknown[] = [], leases: unknown[] = [], guards: JsonObject = {};
  let after: unknown = null, first: JsonObject | null = null;
  do {
    const page = await readCanonicalSnapshot({schema_version: LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA,
      runtime_root: source.runtime_root, goal_id: source.goal_id, include_leases: source.include_leases === true,
      projection_readback: source.projection_readback ?? null, after});
    if (page.status !== "page") return {schema_version: TODO_READ_RESULT, canonical: {
      ...page, schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA}};
    if (first === null) first = page;
    todos.push(...page.todos as unknown[]);
    if (Array.isArray(page.leases)) leases.push(...page.leases);
    Object.assign(guards, page.goal_acceptance_work_guards ?? {});
    after = page.next;
  } while (after !== null);
  const snapshot = requireJsonObject(first!.snapshot, "canonical snapshot identity");
  const canonical: JsonObject = {schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
    status: "loaded", ...requireJsonObject(first!.metadata, "canonical Todo metadata"),
    todos, todo_ids: todos.map(value => requireJsonObject(value, "canonical Todo").todo_id),
    store_identity: snapshot.store_identity,
    ...(source.include_leases === true ? {leases} : {}),
    source_authority: first!.source_authority, provider_revision: snapshot.provider_revision,
    cursor: snapshot.cursor, decision_read_from_provider: true, legacy_fallback_used: false,
    ...(Object.hasOwn(requireJsonObject(first!.metadata, "canonical metadata"), "goal_acceptance_contract") ? {goal_acceptance_work_guards: guards} : {})};
  const projected = await projectCanonicalTodoFields({...input, todos, retained: requireJsonObject(input.query ?? {}, "Todo query").read_scope === "completed_history",
    goal_acceptance_contract: canonical.goal_acceptance_contract ?? null,
    goal_acceptance_work_guards: guards});
  if (input.query == null || projected.rejection) return {...projected, canonical};
  return todoListPayload(input, originalRecords(canonical.todos), projected, canonical);
}

const originalRecords = (value: unknown): JsonObject[] => Array.isArray(value) ? value.map(row => requireJsonObject(row, "Todo source row")) : [];

export async function readHistoricalTodoProjection(input: JsonObject): Promise<JsonObject> {
  const source = requireJsonObject(input.source, "historical Todo read source");
  const canonical = await readLocalCoordinationTodoSource({schema_version: "loopx_local_coordination_todo_source_request_v0",
    runtime_root: source.runtime_root, goal_id: source.goal_id, source: source.original_source});
  if (canonical.status !== "loaded") return {schema_version: TODO_READ_RESULT, canonical};
  return {...await projectCanonicalTodoFields({...input, todos: canonical.todos,
    goal_acceptance_contract: canonical.goal_acceptance_contract ?? null,
    goal_acceptance_work_guards: canonical.goal_acceptance_work_guards ?? null}), canonical};
}

async function todoListPayload(input: JsonObject, original: JsonObject[], fields: JsonObject, canonical: JsonObject): Promise<JsonObject> {
  const query = requireJsonObject(input.query, "Todo list query");
  const sourceName = "file_authority";
  const roles: unknown[] = query.role == null ? ["user", "agent"] : [query.role];
  const select = async (source: JsonObject) => selectTodoReadFields({...input, fields: source.fields,
    field_evaluations: source.field_evaluations, roles, status: query.status ?? null,
    todo_id: query.todo_id ?? null, agent_id: query.agent_id ?? null, limit: query.limit ?? null, source_name: sourceName});
  let selected = await select(fields);
  if (selected.rejection) return selected;
  let projection = requireJsonObject(selected.projection, "Todo read projection");
  if (query.read_scope !== "completed_history" && query.todo_id && !(projection.todos as unknown[]).length) {
    const archived = canonicalTodoReadItems(original).filter(row => row.archive_state === "archive");
    const target = archived.find(row => row.todo_id === query.todo_id);
    if (target && roles.includes(target.role)) {
      const section = target.source_section || "Completed Work Archive";
      const item = structureCanonicalTodo(target, target.role, section);
      const grouped = await projectTodoReadGroup({...input, items: [item], role: item.role, source_section: section,
        lineage: archived, resume_lineage: archived.map(row => structureCanonicalTodo(row, row.role ?? null, row.source_section ?? section)),
        evaluated: false, item_limit: null, full_selection: true});
      if (grouped.rejection) return grouped;
      const key = `${String(target.role)}_todos`, exact = {fields: {[key]: grouped.summary}, field_evaluations: {[key]: grouped.visible_evaluations}};
      selected = await select(exact);
      if (selected.rejection) return selected;
      projection = requireJsonObject(selected.projection, "archived Todo projection");
    }
  }
  let todos = projection.todos as JsonObject[];
  if (query.todo_id && todos.length === 1) {
    const item = todos[0]!;
    const matching = original.filter(row => row.todo_id === query.todo_id && row.role === item.role &&
      (row.archive_state ?? "active") === (item.archive_state ?? "active"));
    if (matching.length !== 1) throw new Error("Todo detail cannot recover an unambiguous full source body");
    item.text = String(matching[0]!.text ?? "");
  }
  const matchedCount = todos.length;
  const hot = query.read_scope !== "completed_history" && query.agent_id && query.limit == null &&
    query.role == null && query.status == null && query.todo_id == null;
  if (hot) {
    const summaries = requireJsonObject(projection.summaries, "Todo summaries");
    for (const [key, summary] of Object.entries(summaries)) summaries[key] = compactTodoReadSummary(
      requireJsonObject(summary, "Todo summary"), key.replace(/_todos$/, ""), null);
    todos = Object.values(summaries).flatMap(value => requireJsonObject(value, "Todo summary").items as JsonObject[]);
  }
  const payload: JsonObject = {ok: true, dry_run: true, read_only: true, command: "list", goal_id: query.goal_id,
    role: query.role ?? "all", status_filter: query.status ?? null, source: sourceName, todo_count: matchedCount,
    todos, state_file: query.state_file, project: query.project ?? null,
    authority_read: {source_authority: canonical.source_authority, provider_revision: canonical.provider_revision,
      cursor: canonical.cursor, todo_read_model: canonical.todo_read_model,
      decision_read_from_provider: true, legacy_fallback_used: false}};
  if (query.agent_id) Object.assign(payload, {agent_id_filter: query.agent_id, unfiltered_todo_count: projection.unfiltered_count,
    filter_semantics: "agent todos include unclaimed items plus claimed_by=<agent>; " +
      "User gates use global_gate, then blocks_agent, then legacy claimed_by scope; " +
      "User actions use bound_agent, then legacy claimed_by scope; unscoped items remain visible"});
  if (hot) Object.assign(payload, {returned_todo_count: todos.length,
    todo_list_projection: todoReadProjectionContract(matchedCount, todos.length, null)});
  if (query.limit != null) Object.assign(payload, {explicit_limit: query.limit,
    unfiltered_todo_count: projection.unfiltered_count, returned_todo_count: todos.length,
    todo_list_projection: todoReadProjectionContract(Number(projection.uncapped_todo_count), todos.length, Number(query.limit))});
  if (query.todo_id) {
    const item = todos[0], relations: JsonObject = {};
    const relationFields = ["claimed_by", "bound_agent", "goal_bound", "blocks_agent", "excluded_agents", "global_gate", "unblocks_todo_id",
      "successor_todo_ids", "completion_continuation", "completion_recovery", "superseded_by", "resume_when", "resume_monitor_generation",
      "resume_condition", "resume_ready", "decision_scope", "required_decision_scopes", "required_write_scopes", "required_capabilities",
      "target_capabilities", "task_class", "action_kind", "continuation_policy", "target_key", "cadence", "next_due_at", "expires_at"];
    if (item) for (const key of relationFields) if (item[key] != null && (!Array.isArray(item[key]) || (item[key] as unknown[]).length)) relations[key] = item[key];
    Object.assign(payload, {todo_id_filter: query.todo_id, matched: todos.length > 0, relations,
      ...(todos.length > 1 ? {ambiguous: true} : {}), ...(todos.length === 0 ? {not_found: true} : {})});
  }
  Object.assign(payload, projection.summaries);
  // The existing formatter is composed here, so a public read never calls back
  // across runtimes for fields of the result it just received.
  const result = query.todo_id ? projectTodoContextPage({detail_payload: payload}) : query.thin ? compactThinTodoRead(payload) :
    query.json_references ? projectTodoContextPage({list_payload: payload}) : payload;
  const shownCount = query.todo_id ? Number(result.todo !== null) : (result.todos as JsonObject[]).length;
  const observed = String(input.read_observed_at);
  result.projection_envelope = sealProjectionEnvelope({schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST,
    projection: "goal_todo_read", observed_at: observed,
    sources: [{source_id: sourceName, read_status: "read", last_read_at: observed,
      item_count: original.length}],
    coverage: {scope: "goal_todo_query", expected_count: projection.uncapped_todo_count,
      included_count: projection.uncapped_todo_count, shown_count: shownCount,
      available_count: projection.uncapped_todo_count, omitted: []}});
  return {schema_version: TODO_READ_RESULT, payload: result,
    ...(canonical.goal_acceptance_oracle_observation_basis ? {canonical_acceptance: {
      goal_acceptance_contract: canonical.goal_acceptance_contract,
      goal_acceptance_oracle_observation_basis: canonical.goal_acceptance_oracle_observation_basis,
    }} : {})};
}
