/** Read-only continuation evidence over the complete Todo graph.
 * These evaluations are derived display state, never completion or execution authority. */
import wire from "./succession_wire_v1.json" with {type: "json"};
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireBoolean, requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";

export const HANDOFF_STATES = ["blocking", "cleared_without_successor", "cleared_with_successor",
  "cleared_no_followup", "superseded", "deferred"] as const;
export type HandoffState = typeof HANDOFF_STATES[number];
const CONTEXT_FIELDS = ["action_kind", "task_repository", "continuation_policy", "claimed_by",
  "completed_at", "updated_at", "required_write_scopes", "required_capabilities", "target_capabilities",
  "explore_result_node_refs", "decision_scope", "required_decision_scopes", "unblocks_todo_id",
  "resume_when", "blocks_agent", "excluded_agents", "global_gate"] as const;
const EVALUATION_SCHEMA = "todo_succession_evaluation_v0";
interface Row {
  facts: JsonObject; id: string | null; status: "open" | "blocked" | "done" | "deferred";
  active: boolean; advancement: boolean; noFollowup: boolean; tracked: boolean;
  successors: string[]; supersededBy: string | null; unblocks: string | null;
  resumes: string | null; handoff: boolean;
  done: boolean; routeFlag: boolean | null; legacyRouteLabel: string;
}
function id(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !/^todo_[a-z0-9_-]{3,64}$/.test(value)) {
    throw new EffectRuntimeRequestError("succession requires normalized Todo identities");
  }
  return value;
}
function decode(value: unknown): Row {
  const raw = requireJsonObject(value, "succession facts");
  const facts: JsonObject = {...raw, context_fields: Array.isArray(raw.context_fields)
    ? CONTEXT_FIELDS.filter(field => (raw.context_fields as unknown[]).includes(field)) : raw.context_fields};
  const status = requireStringLiteral(facts.status, ["open", "blocked", "done", "deferred"], "status");
  const active = requireBoolean(facts.active, "active");
  const advancement = requireBoolean(facts.advancement, "advancement");
  if (typeof facts.legacy_route_label !== "string") {
    throw new EffectRuntimeRequestError("legacy route label must be text");
  }
  if (!Array.isArray(facts.successors) || !Array.isArray(facts.context_fields) ||
      facts.context_fields.some(field => typeof field !== "string")) {
    throw new EffectRuntimeRequestError("succession lists must be arrays");
  }
  return {facts, id: id(facts.todo_id), status, active, advancement,
    noFollowup: requireBoolean(facts.no_followup, "no_followup"),
    tracked: active && status === "done" && advancement &&
      CONTEXT_FIELDS.some(field => (facts.context_fields as unknown[]).includes(field)),
    successors: facts.successors.map(value => {
      const target = id(value);
      if (!target) throw new EffectRuntimeRequestError("successor identity cannot be null");
      return target;
    }), supersededBy: id(facts.superseded_by), unblocks: id(facts.unblocks),
    resumes: id(facts.resumes), handoff: requireBoolean(facts.handoff, "handoff"),
    done: requireBoolean(facts.done, "done"),
    routeFlag: facts.route_flag === null ? null : requireBoolean(facts.route_flag, "route_flag"),
    legacyRouteLabel: facts.legacy_route_label};
}
function handoffState(row: Row, successors: readonly string[]): HandoffState | null {
  if (!row.active || !row.handoff) return null;
  if (row.supersededBy && successors.includes(row.supersededBy)) return "superseded";
  if (row.status === "deferred") return "deferred";
  if (row.status !== "done") return "blocking";
  if (row.noFollowup) return "cleared_no_followup";
  return successors.length ? "cleared_with_successor" : "cleared_without_successor";
}

/** Retain the historical prose hint only as a replan advisory. An explicit
 * boolean wins, including false; this never clears a gate or grants work.
 * Retire the hint when the supported route-closeout writers emit typed flags. */
function routeReplanRequired(row: Row): boolean {
  if (row.routeFlag !== null) return row.routeFlag;
  if (!row.active || !row.handoff || row.done || row.status === "done" || row.status === "deferred") return false;
  const label = row.legacyRouteLabel.toLowerCase();
  return label.includes("stale") && label.includes("handoff") && label.includes("closeout");
}

/** The same edge index drives live readback and bounded archive capture. */
export function indexInferredSuccessors(rows: readonly Pick<Row, "id" | "advancement" | "unblocks" | "resumes">[]): Map<string, string[]> {
  const inferred = new Map<string, string[]>();
  for (const row of rows) {
    if (!row.id || !row.advancement) continue;
    for (const source of new Set([row.unblocks, row.resumes])) {
      if (source && source !== row.id) {
        const targets = inferred.get(source) ?? [];
        targets.push(row.id);
        inferred.set(source, targets);
      }
    }
  }
  return inferred;
}

/** Index inferred edges once; both completion and handoff use the same resolver. */
export function evaluateTodoSuccession(values: readonly unknown[]): JsonObject[] {
  const rows = values.map(decode), byId = new Map<string, Row>();
  const generations = new Set<string>();
  for (const row of rows) {
    if (!row.id) continue;
    const generation = `${row.id}:${row.active ? "active" : "archive"}`;
    if (generations.has(generation)) throw new EffectRuntimeRequestError(`duplicate succession identity: ${row.id}`);
    generations.add(generation);
    // Legacy archive/recreate retains an older record with the same logical id.
    // Only the current active record contributes edges for that identity.
    if (row.active || !byId.has(row.id)) byId.set(row.id, row);
  }
  const inferred = indexInferredSuccessors([...byId.values()]);
  return rows.map(row => {
    const declared = [...new Set([...row.successors, ...(row.supersededBy ? [row.supersededBy] : [])])];
    // A retained archived target is still evidence. A missing/self target is not.
    const resolved = declared.filter(target => target !== row.id && byId.has(target));
    const successors = [...new Set([...resolved, ...(row.id ? inferred.get(row.id) ?? [] : [])])];
    return {schema_version: EVALUATION_SCHEMA, item_sha256: canonicalAuthoritySha256(row.facts),
      successor_todo_ids: successors, unresolved_successor_ids: declared.filter(target => !resolved.includes(target)),
      tracked_completion: row.tracked, successor_gap: row.tracked && !row.noFollowup && successors.length === 0,
      handoff_state: handoffState(row, successors),
      route_continuation_replan_required: routeReplanRequired(row)};
  });
}

/** Filtering may reuse a fresh full-source result, but may not change its item facts. */
export function validateTodoSuccession(facts: unknown, value: unknown): JsonObject {
  const row = decode(facts), evaluation = requireJsonObject(value, "succession evaluation");
  if (evaluation.schema_version !== EVALUATION_SCHEMA || evaluation.item_sha256 !== canonicalAuthoritySha256(row.facts) ||
      !Array.isArray(evaluation.successor_todo_ids) || !Array.isArray(evaluation.unresolved_successor_ids)) {
    throw new EffectRuntimeRequestError("Todo display requires a matching full-source succession evaluation");
  }
  for (const target of [...evaluation.successor_todo_ids, ...evaluation.unresolved_successor_ids]) {
    if (id(target) === null) throw new EffectRuntimeRequestError("successor identity cannot be null");
  }
  requireBoolean(evaluation.tracked_completion, "tracked_completion");
  requireBoolean(evaluation.successor_gap, "successor_gap");
  requireBoolean(evaluation.route_continuation_replan_required, "route_continuation_replan_required");
  if (evaluation.handoff_state !== null) requireStringLiteral(evaluation.handoff_state, HANDOFF_STATES, "handoff_state");
  const successors = evaluation.successor_todo_ids as string[];
  if (evaluation.tracked_completion !== row.tracked ||
      evaluation.successor_gap !== (row.tracked && !row.noFollowup && successors.length === 0) ||
      evaluation.handoff_state !== handoffState(row, successors) ||
      evaluation.route_continuation_replan_required !== routeReplanRequired(row) ||
      new Set(successors).size !== successors.length || successors.includes(row.id ?? "")) {
    throw new EffectRuntimeRequestError("inconsistent Todo succession evaluation");
  }
  return evaluation;
}

/** Co-deployed transport contract: columns compress repeated keys, never rows.
 * Decode before semantic validation so a positional mismatch cannot change meaning. */
export const SUCCESSION_FACT_COLUMNS: readonly string[] = wire.fact_columns;
export const SUCCESSION_EVALUATION_COLUMNS: readonly string[] = wire.evaluation_columns;

function decodeColumns(values: unknown, columns: unknown, expected: readonly string[], label: string): JsonObject[] {
  if (!Array.isArray(columns) || columns.length !== expected.length ||
      columns.some((name, index) => name !== expected[index])) {
    throw new EffectRuntimeRequestError(`succession ${label} columns mismatch`);
  }
  if (!Array.isArray(values)) throw new EffectRuntimeRequestError(`succession ${label} must be an array`);
  return values.map(value => {
    if (!Array.isArray(value) || value.length !== expected.length) {
      throw new EffectRuntimeRequestError(`succession ${label} row width mismatch`);
    }
    return Object.fromEntries(expected.map((name, index) => [name, value[index]]));
  });
}

export function projectTodoSuccession(value: unknown): JsonObject {
  const request = requireJsonObject(value, "Todo succession request");
  if (request.schema_version !== wire.request_schema) {
    throw new EffectRuntimeRequestError("Todo succession request schema mismatch");
  }
  const facts = decodeColumns(request.rows, request.row_columns, SUCCESSION_FACT_COLUMNS, "facts");
  const contexts = request.context_field_sets;
  if (!Array.isArray(contexts) || contexts.some(value =>
      !Array.isArray(value) || value.some(field => typeof field !== "string"))) {
    throw new EffectRuntimeRequestError("invalid succession context field sets");
  }
  const rows = facts.map(row => {
    const index = row.context_fields;
    if (typeof index !== "number" || !Number.isSafeInteger(index) || index < 0 || index >= contexts.length) {
      throw new EffectRuntimeRequestError("invalid succession context field index");
    }
    return {...row, context_fields: contexts[index]};
  });
  let results: JsonObject[];
  if (request.evaluations !== undefined) {
    const evaluations = decodeColumns(request.evaluations, request.evaluation_columns, SUCCESSION_EVALUATION_COLUMNS, "evaluations");
    if (evaluations.length !== rows.length) throw new EffectRuntimeRequestError("succession evaluation cardinality mismatch");
    results = rows.map((row, index) => validateTodoSuccession(row, evaluations[index]));
  } else {
    if (request.evaluation_columns !== undefined) throw new EffectRuntimeRequestError("unexpected succession evaluation columns");
    results = evaluateTodoSuccession(rows);
  }
  return {schema_version: wire.result_schema, evaluation_columns: [...SUCCESSION_EVALUATION_COLUMNS],
    evaluations: results.map(row => SUCCESSION_EVALUATION_COLUMNS.map(name => row[name]!))};
}

/** Summary proofs are derived from every selected row before display caps.
 * A query subset can describe items but cannot certify closure of the source. */
export function projectTodoClosure(value: unknown): JsonObject {
  const request = requireJsonObject(value, "Todo closure request");
  if (request.schema_version !== "todo_closure_request_v0" || !Array.isArray(request.rows)) {
    throw new EffectRuntimeRequestError("Todo closure request schema mismatch");
  }
  const source = typeof request.source_section === "string" ? request.source_section : "";
  const role = request.role;
  const valid = (role === "user" || role === "agent") && source.trim() !== "" &&
    requireBoolean(request.full_selection, "full_selection");
  const rows = request.rows.map(value => {
    const row = requireJsonObject(value, "closure row");
    return {status: requireStringLiteral(row.status, ["open", "blocked", "done", "deferred"], "status"),
      watch: requireBoolean(row.watch_only, "watch_only"), noFollowup: requireBoolean(row.no_followup, "no_followup"),
      gap: requireBoolean(row.successor_gap, "successor_gap"), replan: requireBoolean(row.replan, "replan"),
      handoff: row.handoff_state === null ? null : requireStringLiteral(row.handoff_state, HANDOFF_STATES, "handoff_state")};
  });
  const noFollowup = rows.filter(row => (row.status === "done" || row.status === "deferred") && row.noFollowup).length;
  const watches = rows.filter(row => row.status !== "done" && row.status !== "deferred" && row.watch).length;
  const convergent = rows.filter(row => row.status !== "done" && row.status !== "deferred" && !row.watch).length;
  const result: JsonObject = {};
  if (valid && convergent === 0 && rows.every(row => row.status !== "deferred")) {
    result.source_proof = {schema_version: "todo_source_proof_v0", role, item_count: rows.length, derived: true};
  }
  if (valid && rows.every(row => (row.status === "done" || row.watch) && row.status !== "deferred" &&
      !row.gap && !row.replan && row.handoff !== "cleared_without_successor" && row.handoff !== "blocking")) {
    result.terminal_closure_proof = {schema_version: "todo_terminal_closure_proof_v0", role,
      source_section: source, item_count: rows.length, all_todos_done: watches === 0,
      monitor_open_count: watches, successor_gap_count: 0, route_replan_count: 0,
      no_followup_count: noFollowup, derived: true,
      ...(watches ? {all_convergent_todos_done: true, watch_only_monitor_count: watches} : {})};
  }
  if (noFollowup) result.closure_intent = {schema_version: "todo_closure_intent_v0", kind: "no_followup", derived: true, count: noFollowup};
  return result;
}

/** Validate retained source witnesses at the quota read boundary. Malformed
 * evidence is an invalid observation, never an exception or closure authority.
 * A bounded display may retain a full-source proof but cannot manufacture it. */
export function validateTodoClosureSource(value: unknown): JsonObject {
  const source = requireJsonObject(value, "Todo closure source");
  const proof = jsonObject(source.source_proof), terminal = jsonObject(source.terminal_closure_proof);
  const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
  const total = source.total_count, open = source.open_count, done = source.done_count, deferred = source.deferred_count;
  const countsValid = count(total) && count(open) && count(done) && count(deferred) && total === open + done + deferred;
  const sourceValid = proof !== null && proof.schema_version === "todo_source_proof_v0" &&
    (proof.role === "user" || proof.role === "agent") && proof.derived === true &&
    typeof source.source_section === "string" && source.source_section.trim() !== "" &&
    count(proof.item_count) && proof.item_count === total;
  const rows = source.items, monitors = source.monitor_open_items;
  const covered = count(total) && Array.isArray(rows) &&
    (total === 0 ? rows.length === 0 : rows.length > 0 && rows.length <= total);
  const rowsValid = Array.isArray(rows) && rows.every(value => {
    const row = jsonObject(value);
    return row !== null && ((row.status === "done" && row.done === true) || row.watch_only === true) &&
      row.route_continuation_replan_required !== true;
  });
  const monitorsValid = Array.isArray(monitors) && monitors.every(value => jsonObject(value)?.watch_only === true);
  const terminalValid = countsValid && sourceValid && source.schema_version === "todo_summary_v0" &&
    covered && rowsValid && monitorsValid && source.deferred_item_count === 0 && source.deferred_resume_count === 0 &&
    source.convergence_open_count === 0 && source.completed_without_successor_count === 0 && source.route_continuation_replan_count === 0 &&
    terminal !== null && terminal.schema_version === "todo_terminal_closure_proof_v0" && terminal.role === proof!.role &&
    terminal.source_section === source.source_section && count(terminal.item_count) && terminal.item_count === total &&
    (terminal.all_todos_done === true || terminal.all_convergent_todos_done === true) &&
    count(terminal.monitor_open_count) && count(terminal.watch_only_monitor_count) &&
    terminal.monitor_open_count === terminal.watch_only_monitor_count &&
    terminal.successor_gap_count === 0 && terminal.route_replan_count === 0 &&
    count(terminal.no_followup_count) && terminal.derived === true;
  const intent = jsonObject(source.closure_intent);
  const intentValid = terminalValid && intent !== null && intent.schema_version === "todo_closure_intent_v0" &&
    intent.kind === "no_followup" && intent.derived === true && count(intent.count) && count(done) &&
    intent.count > 0 && intent.count <= done && intent.count === terminal!.no_followup_count;
  return {source_completeness: {schema_version: "todo_source_completeness_v0",
    status: terminalValid ? "valid" : "invalid", source: "structured_todo_projection",
    role: proof?.role ?? null, terminal_closure: terminalValid ? "valid" : "invalid"},
    closure_intent: intentValid ? {...intent, source: "todo_no_followup"} : null};
}
