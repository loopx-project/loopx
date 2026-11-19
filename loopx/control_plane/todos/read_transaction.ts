/** Canonical provider IO and full-source summaries share one read transaction. */
import type {JsonObject} from "../effect_program.ts";
import {requireBoolean, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {readCanonicalSnapshot} from "../coordination/canonical_snapshot_page.ts";
import {readLocalCoordinationTodoSource} from "../coordination/local_authority_read.ts";
import {LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA} from "../coordination/coordination_state_contract.generated.ts";
import {projectCompletionEvidence, projectCanonicalTodoFields, selectTodoReadFields, projectTodoReadGroup, TODO_READ_RESULT} from "./read_projection.ts";
import {id, canonicalTodoReadItems, structureCanonicalTodo} from "./read_codec.ts";
import {compactTodoReadSummary, compactThinTodoRead, compactTodoQueueSummary, todoReadProjectionContract} from "./read_presentation.ts";
import {projectTodoContextPage, SUMMARY_SOURCES, PLANNING_SOURCES} from "./context_projection.ts";
import {SOURCE_KEYS as RESUME_SOURCES} from "./resume_planning.ts";
import {sealProjectionEnvelope, PROJECTION_ENVELOPE_SEAL_REQUEST} from "../projection_envelope.ts";
import {canonicalAuthoritySha256, canonicalAuthorityJson} from "../coordination/authority_store_codec.ts";
import {decodeTodoReadSource} from "./read_codec.ts";
import {projectTodoQuotaPlanning} from "./quota_selection.ts";
import type {LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";

const includesArchivedRows = (query: JsonObject): boolean =>
  query.read_scope === "completed_history" || query.include_archived === true;
const canonicalSourceRows = new Set<string>([...SUMMARY_SOURCES, ...PLANNING_SOURCES, ...RESUME_SOURCES]);

export async function readCanonicalTodoProjection(input: JsonObject, dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  const source = requireJsonObject(input.source, "canonical Todo read source");
  const contractFields: unknown = input.canonical_contract_fields;
  if (contractFields !== undefined && (
    input.kind !== "canonical_fields" || input.query != null || input.read_pipeline !== undefined ||
    input.return_projection === true || source.include_leases === true || source.projection_readback != null ||
    !Array.isArray(contractFields) || !contractFields.length || new Set(contractFields).size !== contractFields.length ||
    contractFields.some(key => typeof key !== "string" || !/^[a-z][a-z0-9_]*$/.test(key))
  )) throw new EffectRuntimeRequestError("Todo contract fields require a read-only canonical summary");
  const canonical = await collectCanonicalTodoSource(source, dependencies);
  if (canonical.status !== "loaded") return {schema_version: TODO_READ_RESULT, canonical};
  const todos = canonical.todos;
  const guards = canonical.goal_acceptance_work_guards ?? {};

  const query = requireJsonObject(input.query ?? {}, "Todo query");
  const includeArchived = includesArchivedRows(query);
  const retained = query.read_scope === "completed_history";
  let projected = await projectCanonicalTodoFields({...input, todos, retained, include_archived: includeArchived,
    goal_acceptance_contract: canonical.goal_acceptance_contract ?? null,
    goal_acceptance_work_guards: guards});
  if (input.read_pipeline !== undefined) {
    if (input.kind !== "canonical_fields" || input.query != null || !Array.isArray(input.read_pipeline) || !input.read_pipeline.length) {
      throw new EffectRuntimeRequestError("Todo select pipeline requires a complete canonical read");
    }
    for (const value of input.read_pipeline) {
      if (projected.rejection) return {...projected, canonical};
      const stage = requireJsonObject(value, "Todo select pipeline stage");
      validateCanonicalSource(canonical, requireJsonObject(stage.expected_source, "Todo select source identity"));
      const selector = requireJsonObject(stage.selector, "Todo native selector");
      const references = requireJsonObject(stage.sources, "Todo select source references");
      if (!Array.isArray(selector.roles) || Object.keys(references).length !== selector.roles.length) {
        throw new EffectRuntimeRequestError("Todo select pipeline must bind every consumed role");
      }
      const fields: JsonObject = {};
      for (const role of selector.roles) {
        const key = `${String(role)}_todos`;
        fields[key] = resolveCanonicalSource(requireJsonObject(projected.fields, "Todo select fields"),
          requireJsonObject(references[key], "Todo select source"), key);
      }
      const selected = await selectTodoReadFields({...selector, fields,
        field_evaluations: projected.field_evaluations, include_source_evaluations: true});
      if (selected.rejection) return {...selected, canonical};
      projected = {...selected, fields: requireJsonObject(selected.projection, "native Todo selection").summaries};
    }
  }
  if (input.query == null || projected.rejection) {
    // Both the summary and contract facts come from the complete checked
    // population. Only the diagnostic facts need to leave this transaction.
    let canonicalResult = canonical, replay = {};
    if (Array.isArray(contractFields)) {
      const {todos: rows, todo_ids: _ids, ...metadata} = canonical;
      canonicalResult = {...metadata, todo_contract_rows: originalRecords(rows).map(row =>
        Object.fromEntries(contractFields.map(key => [key, Object.hasOwn(row, key) ? row[key] : null])))};
      const {canonical_contract_fields: _contractFields, read_observed_at: _readObserved, ...replayRead} = input;
      replay = {replay_read: replayRead};
    }
    return {
      ...(input.return_projection === true && projected.projection
        ? {schema_version: TODO_READ_RESULT, projection: projected.projection, field_evaluations: projected.field_evaluations}
        : {...projected, canonical: canonicalResult}),
      ...(projected.fields && !projected.rejection ? {source_projection: {source_row_fields: [...canonicalSourceRows],
        source: Object.fromEntries(["source_authority", "store_identity", "provider_revision", "cursor"].map(key => [key, canonical[key]])),
        ...replay, field_digests: Object.fromEntries(Object.entries(requireJsonObject(projected.fields, "Todo fields"))
          .map(([key, field]) => [key, canonicalAuthoritySha256(field)]))}} : {})};
  }
  return todoListPayload(input, originalRecords(canonical.todos), projected, canonical);
}

function validateCanonicalSource(canonical: JsonObject, expected: JsonObject): void {
  if (canonical.status !== "loaded" || Object.keys(expected).length !== 4 ||
      ["source_authority", "store_identity", "provider_revision", "cursor"].some(field => expected[field] !== canonical[field])) {
    throw new EffectRuntimeRequestError("quota canonical source changed; restart the complete guarded read");
  }
}

/** Full-tree transport, with provider facts immutable. Independent additions and
 * the acceptance owner's observation do not acquire provider provenance. */
function resolveCanonicalSource(fields: JsonObject, reference: JsonObject, expectedField?: string, fieldDigests?: JsonObject): JsonObject {
  if ((reference.field !== "agent_todos" && reference.field !== "user_todos") ||
      expectedField !== undefined && reference.field !== expectedField) {
    throw new EffectRuntimeRequestError("quota source field must name the consumed Todo role");
  }
  const field = requireJsonObject(fields[reference.field], "quota source field");
  if ((fieldDigests?.[reference.field] ?? canonicalAuthoritySha256(field)) !== reference.field_sha256) {
    throw new EffectRuntimeRequestError("quota source projection no longer matches the original complete read");
  }
  const record = JSON.parse(JSON.stringify(field)) as JsonObject;
  if (!Array.isArray(reference.changes)) throw new EffectRuntimeRequestError("quota source changes must be an array");
  for (const raw of reference.changes) {
    const change = requireJsonObject(raw, "quota source change");
    if (!Array.isArray(change.path) || !change.path.length ||
        change.path.some(key => typeof key !== "string" && !(typeof key === "number" && Number.isSafeInteger(key) && key >= 0)) ||
        (change.delete === true) === Object.hasOwn(change, "value")) {
      throw new EffectRuntimeRequestError("invalid complete-source change");
    }
    const path = change.path as (string | number)[];
    let parent: any = record;
    for (const key of path.slice(0, -1)) {
      if (parent === null || typeof parent !== "object" || !Object.hasOwn(parent, key)) {
        throw new EffectRuntimeRequestError("quota source change path is absent");
      }
      parent = parent[key];
    }
    const last = path.at(-1)!;
    if (parent === null || typeof parent !== "object" || Array.isArray(parent) &&
        (typeof last !== "number" || last >= parent.length || change.delete === true)) {
      throw new EffectRuntimeRequestError("quota source change parent is invalid");
    }
    if (change.delete === true) {
      if (!Object.hasOwn(parent, last)) throw new EffectRuntimeRequestError("quota source deletion is absent");
      delete parent[last];
    } else Object.defineProperty(parent, last, {value: change.value, enumerable: true, writable: true, configurable: true});
  }
  // JSON cloning owns containers while retaining primitive values. Exact
  // equality already proves an unchanged primitive; containers and altered
  // scalar values retain their full canonical digest comparison.
  for (const [key, value] of Object.entries(field)) {
    const unchangedScalar = value === record[key] && (value === null || typeof value !== "object");
    if (key !== "goal_acceptance_contract" && (!Object.hasOwn(record, key) ||
        !unchangedScalar && canonicalAuthoritySha256(value) !== canonicalAuthoritySha256(record[key]))) {
      throw new EffectRuntimeRequestError("canonical Todo facts cannot be replaced by caller source differences");
    }
  }
  if ([...canonicalSourceRows].some(key => Object.hasOwn(record, key) && !Object.hasOwn(field, key))) {
    throw new EffectRuntimeRequestError("canonical Todo facts cannot be added in caller source lanes");
  }
  if (!Array.isArray(reference.python_values ?? []) || (reference.python_values as unknown[][] | undefined)?.some(fact =>
      !Array.isArray(fact) || !Array.isArray(fact[0]) || !fact[0].length ||
      Object.hasOwn(field, fact[0][0]) && fact[0][0] !== "goal_acceptance_contract")) {
    throw new EffectRuntimeRequestError("canonical Todo scalar facts cannot be replaced by caller source differences");
  }
  return decodeTodoReadSource({record, python_values: reference.python_values ?? []});
}

/** Resolve complete source differences against one freshly validated provider
 * revision. Nothing is retained between RPCs; supplied evidence remains supplied. */
export async function readCanonicalQuotaProjection(input: JsonObject, dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  if (!Array.isArray(input.requests) || !input.requests.length || input.requests.length > 2) {
    throw new EffectRuntimeRequestError("quota projection requires one or two explicit source requests");
  }
  const reads = new Map<string, JsonObject>();
  const wires: JsonObject[] = [];
  for (const value of input.requests) {
    let request = requireJsonObject(value, "quota projection request");
    const summary = requireJsonObject(request.summary_request, "quota summary request");
    if (summary.source_reference !== undefined) {
      const reference = requireJsonObject(summary.source_reference, "quota source reference");
      const read = requireJsonObject(reference.read, "quota source read");
      if (read.kind !== "canonical_fields" || read.query != null || read.canonical_contract_fields !== undefined) {
        throw new EffectRuntimeRequestError("quota source requires the original complete canonical read");
      }
      const expected = requireJsonObject(reference.expected_source, "quota source identity");
      const key = JSON.stringify(canonicalAuthorityJson({read, expected}));
      let projected = reads.get(key);
      if (projected === undefined) {
        projected = await readCanonicalTodoProjection(read, dependencies);
        const canonical = requireJsonObject(projected.canonical, "quota canonical source");
        if (canonical.status !== "loaded" || projected.rejection) return {...projected, schema_version: TODO_READ_RESULT};
        validateCanonicalSource(canonical, expected);
        reads.set(key, projected);
      }
      // These digests were produced with these fields by the fresh read above;
      // no caller-supplied digest or field survives between requests.
      const fieldDigests = requireJsonObject(requireJsonObject(projected.source_projection,
        "quota source projection").field_digests, "quota source field digests");
      const source = {record: resolveCanonicalSource(requireJsonObject(projected.fields, "quota source fields"), reference, undefined, fieldDigests),
        python_values: reference.python_values ?? []};
      request = {...request, summary_request: {...summary, source}};
    }
    wires.push(projectTodoQuotaPlanning(request));
  }
  return {schema_version: TODO_READ_RESULT, quota_wires: wires};
}

async function collectCanonicalTodoSource(source: JsonObject, dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  const todos: unknown[] = [], leases: unknown[] = [], guards: JsonObject = {};
  let after: unknown = null, first: JsonObject | null = null;
  do {
    const page = await readCanonicalSnapshot({schema_version: LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA,
      runtime_root: source.runtime_root, goal_id: source.goal_id, include_leases: source.include_leases === true,
      projection_readback: source.projection_readback ?? null, after}, dependencies);
    if (page.status !== "page") return {
      ...page, schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA};
    if (first === null) first = page;
    todos.push(...page.todos as unknown[]);
    if (Array.isArray(page.leases)) leases.push(...page.leases);
    Object.assign(guards, page.goal_acceptance_work_guards ?? {});
    after = page.next;
  } while (after !== null);
  const snapshot = requireJsonObject(first!.snapshot, "canonical snapshot identity");
  return {schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
    status: "loaded", ...requireJsonObject(first!.metadata, "canonical Todo metadata"),
    todos, todo_ids: todos.map(value => requireJsonObject(value, "canonical Todo").todo_id),
    store_identity: snapshot.store_identity,
    ...(source.include_leases === true ? {leases} : {}),
    source_authority: first!.source_authority, provider_revision: snapshot.provider_revision,
    cursor: snapshot.cursor, decision_read_from_provider: true, legacy_fallback_used: false,
    ...(Object.hasOwn(requireJsonObject(first!.metadata, "canonical metadata"), "goal_acceptance_contract") ? {goal_acceptance_work_guards: guards} : {})};
}

const originalRecords = (value: unknown): JsonObject[] => Array.isArray(value) ? value.map(row => requireJsonObject(row, "Todo source row")) : [];

/** Read the selected Todo and lease facts at one checked provider revision.
 * Reuse full collection validation; only the fields used by preparation cross
 * the caller boundary. This read acquires no lease and grants no readiness. */
export async function readExecutionPreparationSource(input: JsonObject, dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  const source = requireJsonObject(input.source, "execution preparation source");
  const goalId = requireNonEmptyString(source.goal_id, "goal_id");
  const todoId = id(input.todo_id);
  if (!todoId || todoId !== input.todo_id) throw new EffectRuntimeRequestError("execution preparation requires an exact Todo id");
  const canonical = await collectCanonicalTodoSource({
    runtime_root: source.runtime_root, goal_id: goalId, include_leases: true,
  }, dependencies);
  const unavailable = {schema_version: TODO_READ_RESULT, execution_preparation_source: null};
  if (canonical.status !== "loaded") return unavailable;
  const todo = originalRecords(canonical.todos).find(row => row.todo_id === todoId);
  if (!todo) return unavailable;
  const leases = originalRecords(canonical.leases).filter(row => row.todo_id === todoId)
    .map(({todo_id, schema_version, status, owner, expires_at, idempotency_key, version}) =>
      ({todo_id, schema_version, status, owner, expires_at, idempotency_key, version}));
  return {schema_version: TODO_READ_RESULT, execution_preparation_source: {
    goal_id: goalId, todo_id: todoId, handoff_mode: canonical.handoff_mode,
    source_authority: canonical.source_authority, provider_revision: canonical.provider_revision,
    decision_read_from_provider: canonical.decision_read_from_provider,
    legacy_fallback_used: canonical.legacy_fallback_used,
    todo: {todo_id: todoId, status: todo.status, claimed_by: todo.claimed_by}, leases,
  }};
}

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
  const includeArchived = includesArchivedRows(query);
  const fullText = query.full_text === undefined ? false : requireBoolean(query.full_text, "full_text");
  const summary = query.summary === undefined ? false : requireBoolean(query.summary, "Todo summary view");
  if (fullText && (query.thin === true || query.json_references === true || summary))
    throw new EffectRuntimeRequestError("Full Todo body reads cannot use compact presentation");
  if (summary && (query.thin || query.todo_id)) throw new EffectRuntimeRequestError("Todo summary cannot be combined with thin or exact reads");
  const sourceName = requireNonEmptyString(canonical.source_authority, "canonical Todo source authority");
  const roles: unknown[] = query.role == null ? ["user", "agent"] : [query.role];
  const select = async (source: JsonObject) => selectTodoReadFields({...input, fields: source.fields,
    field_evaluations: source.field_evaluations, roles, status: query.status ?? null,
    // The queue formatter applies its own row cap and needs the unshortened titles.
    todo_id: query.todo_id ?? null, agent_id: query.agent_id ?? null, limit: summary ? null : query.limit ?? null, source_name: sourceName});
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
  // Restore only already-selected bodies. Scope, status, archive and limits
  // remain the existing owner's decisions; all bodies share this source head.
  if (fullText) {
    const identity = (row: JsonObject) => JSON.stringify([row.todo_id, row.role, row.archive_state ?? "active"]);
    const bodies = new Map<string, JsonObject | null>();
    for (const row of original) {
      const key = identity(row);
      bodies.set(key, bodies.has(key) ? null : row);
    }
    for (const item of todos) {
      const source = bodies.get(identity(item));
      if (!source) throw new Error("Todo detail cannot recover an unambiguous full source body");
      item.text = String(source.text ?? "");
    }
  } else if (query.todo_id && todos.length === 1) {
    const item = todos[0]!;
    const matching = original.filter(row => row.todo_id === query.todo_id && row.role === item.role &&
      (row.archive_state ?? "active") === (item.archive_state ?? "active"));
    if (matching.length !== 1) throw new Error("Todo detail cannot recover an unambiguous full source body");
    item.text = String(matching[0]!.text ?? "");
  }
  const matchedCount = todos.length;
  const hot = !fullText && !includeArchived && query.agent_id && query.limit == null &&
    query.role == null && query.status == null && query.todo_id == null;
  if (hot && !summary) {
    const summaries = requireJsonObject(projection.summaries, "Todo summaries");
    for (const [key, summary] of Object.entries(summaries)) summaries[key] = compactTodoReadSummary(
      requireJsonObject(summary, "Todo summary"), key.replace(/_todos$/, ""), null);
    todos = Object.values(summaries).flatMap(value => requireJsonObject(value, "Todo summary").items as JsonObject[]);
  }
  const payload: JsonObject = {ok: true, dry_run: true, read_only: true, command: "list", goal_id: query.goal_id,
    role: query.role ?? "all", status_filter: query.status ?? null, source: sourceName, todo_count: matchedCount,
    todos, ...(fullText ? {full_text: true} : {}), ...(query.include_archived === true ? {include_archived: true} : {}),
    state_file: query.state_file, project: query.project ?? null,
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
  const result = summary ? compactTodoQueueSummary(payload, Boolean(hot)) :
    query.todo_id ? projectTodoContextPage({detail_payload: payload}) : query.thin ? compactThinTodoRead(payload) :
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
  const completeSource = !query.role && !query.status && !query.todo_id && !query.agent_id && query.limit == null &&
    !query.thin && !query.json_references && !summary && !fullText && !includeArchived;
  const sourceFields = requireJsonObject(fields.fields, "canonical Todo fields");
  const sourceIdentity = Object.fromEntries(["source_authority", "store_identity", "provider_revision", "cursor"].map(key => [key, canonical[key]]));
  const {query: _query, read_observed_at: _readObserved, ...producer} = input;
  return {schema_version: TODO_READ_RESULT, payload: result,
    ...(completeSource ? {source_projection: {source: sourceIdentity, source_row_fields: [...canonicalSourceRows],
      field_digests: Object.fromEntries(["user_todos", "agent_todos"].map(key => [key, canonicalAuthoritySha256(result[key])])),
      replay_read: {...producer, kind: "canonical_fields", read_pipeline: [{expected_source: sourceIdentity,
        sources: Object.fromEntries(["user_todos", "agent_todos"].map(field => [field,
          {field, field_sha256: canonicalAuthoritySha256(sourceFields[field]), changes: [], python_values: []}])),
        selector: {roles, status: null, todo_id: null, agent_id: null, limit: null, source_name: sourceName,
          observed_at: input.observed_at, evaluated_at: input.evaluated_at}}]}}} : {}),
    ...(canonical.goal_acceptance_oracle_observation_basis ? {canonical_acceptance: {
      goal_acceptance_contract: canonical.goal_acceptance_contract,
      goal_acceptance_oracle_observation_basis: canonical.goal_acceptance_oracle_observation_basis,
    }} : {})};
}

export async function readCompletionEvidence(input: JsonObject): Promise<JsonObject> {
  let facts = input;
  if (input.source != null) {
    const canonical = await collectCanonicalTodoSource(requireJsonObject(input.source, "completion evidence source"));
    if (canonical.status !== "loaded") return {schema_version: TODO_READ_RESULT, canonical};
    const records = originalRecords(canonical.todos), expected = id(input.expected_todo_id) ?? input.expected_todo_id;
    const todo = records.find(row => id(row.todo_id) === expected);
    if (!todo) return {schema_version: TODO_READ_RESULT, completion_error:
      `durable completion todo_id '${String(expected)}' was not found in canonical provider state`};
    facts = {...input, todo, existing_todo_ids: records.map(row => id(row.todo_id)).filter(Boolean)};
  }
  return {schema_version: TODO_READ_RESULT, completion: projectCompletionEvidence(facts)};
}
