/** Read-only decision dependency rules over one complete source snapshot.
 * A consistent dependency is not approval, a lease, or a mutation receipt. */
import {stripPythonWhitespace} from "../coordination/python_whitespace.ts";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, optionalNonEmptyString, requireBoolean, requireInteger} from "../runtime_decode.ts";
import {gateAddressesAgent} from "./agent_scope.ts";
import {
  TODO_DECISION_SCOPE_GRANULARITY_SET,
  TODO_DECISION_SCOPE_KEY_PATTERN,
  TODO_DECISION_SCOPE_KIND_SET,
} from "./decision_metadata.ts";

export const DECISION_SCOPE_REQUEST_SCHEMA = "todo_decision_scope_request_v0";
const RANK: Readonly<Record<string, number>> = {action: 0, lane: 1, goal: 2, project: 3, global: 4};
const object = (value: unknown): value is JsonObject => value !== null && typeof value === "object" && !Array.isArray(value);
const rows = (value: unknown): JsonObject[] => {
  if (!Array.isArray(value)) throw new TypeError("decision scope rows must be an array");
  return value.map(item => requireJsonObject(item, "decision scope row"));
};
const text = (value: unknown): string | null => typeof value === "string" && value ? value : null;

type ValidDecisionScope = JsonObject & {scope_key: string};

function validDecisionScope(scope: JsonObject): scope is ValidDecisionScope {
  return TODO_DECISION_SCOPE_KIND_SET.has(String(scope.kind)) &&
    TODO_DECISION_SCOPE_GRANULARITY_SET.has(String(scope.granularity)) &&
    Object.hasOwn(RANK, String(scope.granularity)) && typeof scope.scope_key === "string" &&
    TODO_DECISION_SCOPE_KEY_PATTERN.test(scope.scope_key);
}

function validScopeCovers(gate: ValidDecisionScope, required: ValidDecisionScope): boolean {
  return gate.kind === required.kind &&
    (gate.scope_key === "*" || gate.scope_key === required.scope_key) && RANK[String(gate.granularity)] >= RANK[String(required.granularity)];
}

export function decisionScopeCovers(gate: unknown, required: unknown): boolean {
  if (!object(gate) || !object(required)) return false;
  return validDecisionScope(gate) && validDecisionScope(required) && validScopeCovers(gate, required);
}

function addressed(gate: JsonObject, agent: string | null): boolean {
  return gateAddressesAgent({global: gate.global_gate === true, blocks: text(gate.blocks_agent), claim: text(gate.claimed_by)}, agent);
}

type ScopedStandingAuthority = JsonObject & {entries: JsonObject[]; conflicts?: JsonObject[]};

export function scopeStandingAuthority(value: unknown, agent: string | null): ScopedStandingAuthority | null {
  if (!object(value) || !Array.isArray(value.entries)) return null;
  const entries = rows(value.entries).filter(item => addressed(item, agent));
  const conflicts = rows(value.conflicts ?? []).filter(item => addressed(item, agent));
  if (!entries.length && !conflicts.length) return null;
  return {schema_version: "standing_decision_authority_v0", agent_id: agent,
    active_count: entries.filter(e => e.active === true).length,
    inactive_count: entries.filter(e => e.active !== true).length, entries,
    ...(conflicts.length ? {conflicts, conflict_count: conflicts.length} : {})};
}

export function decisionScopeRelation(gate: JsonObject, item: JsonObject): JsonObject | null {
  const scope = gate.decision_scope;
  if (!object(scope)) return null;
  const required = rows(item.required_decision_scopes ?? []);
  const matched = required.find(value => decisionScopeCovers(scope, value));
  return {schema_version: "decision_scope_relation_v0", source: "decision_scope",
    state: matched ? "gate_covers_action" : "independent",
    ...(!required.length ? {reason: "agent_todo_has_no_required_decision_scopes"} : {}),
    gate_todo_id: gate.todo_id ?? null, agent_todo_id: item.todo_id ?? null,
    decision_scope: scope, ...(matched ? {matched_required_decision_scope: matched} : {}), required_decision_scopes: required};
}

export function exactTodoGateRelation(gate: JsonObject, item: JsonObject): JsonObject | null {
  const target = text(gate.unblocks_todo_id);
  if (!target) return null;
  return {schema_version: "todo_gate_relation_v0", source: "unblocks_todo_id",
    state: target === item.todo_id ? "gate_targets_todo" : "independent",
    gate_todo_id: gate.todo_id ?? null, target_todo_id: target, agent_todo_id: item.todo_id ?? null};
}

export function todoGateRelation(gate: JsonObject, item: JsonObject): JsonObject | null {
  const exact = exactTodoGateRelation(gate, item), scope = decisionScopeRelation(gate, item);
  if (exact?.state === "independent" && scope?.state === "gate_covers_action") {
    return {schema_version: "todo_gate_relation_v0", source: "unblocks_todo_id+decision_scope",
      state: "projection_repair_required", reason: "decision_scope_covers_agent_todo_but_unblocks_todo_targets_different_todo",
      gate_todo_id: exact.gate_todo_id, target_todo_id: exact.target_todo_id,
      agent_todo_id: exact.agent_todo_id, exact_todo_relation: exact, decision_scope_relation: scope};
  }
  if (exact?.state === "gate_targets_todo") return {...exact, ...(scope ? {decision_scope_relation: scope} : {})};
  if (scope?.state === "gate_covers_action") return {...scope, ...(exact ? {exact_todo_relation: exact} : {})};
  return exact ? {...exact, ...(scope ? {decision_scope_relation: scope} : {})} : scope;
}

/** Legacy action labels are exact keys, not natural-language permission rules.
 * Missing scope is insufficient evidence for safe bypass, never a global gate. */
export function fallbackGateRelation(gate: JsonObject, item: JsonObject): JsonObject {
  if (gate.global_gate === true) return {source: "global_gate", state: "gate_covers_action"};
  const explicit = todoGateRelation(gate, item);
  if (explicit) return explicit;
  const action = (row: JsonObject) => typeof row.action_kind === "string" ? row.action_kind.trim().toLowerCase() : "";
  const a = action(gate), b = action(item);
  return {schema_version: "todo_gate_relation_v0", gate_todo_id: gate.todo_id ?? null,
    agent_todo_id: item.todo_id ?? null,
    source: a && a === b ? "legacy_exact_action_kind" : "missing_dependency_scope",
    state: a && a === b ? "gate_covers_action" : "projection_repair_required",
    ...(!a || a !== b ? {reason: "safe_fallback_requires_explicit_dependency_scope"} : {})};
}

/** Select only from already evaluated lanes. Never re-evaluate waits against a
 * compact list or refill an authoritative empty capability result from backlog.
 * Return source positions, leaving display compaction and wording to adapters. */
export function selectScopedGateFallback(request: JsonObject): JsonObject | null {
  const agent = optionalNonEmptyString(request.agent_id, "agent_id");
  const requested = optionalNonEmptyString(request.requested_todo_id, "requested_todo_id");
  const debt = requireBoolean(request.monitor_debt_backoff_active, "monitor_debt_backoff_active");
  const allowUnrelated = requireBoolean(request.allow_unrelated_gate, "allow_unrelated_gate");
  const gates = rows(request.gates).map((gate, index) => ({gate, index})).filter(({gate}) =>
    gate.done !== true && ["open", "blocked"].includes(String(gate.status || "open")) &&
    gate.archive_state !== "archive" && (!agent || addressed(gate, agent)));
  if (!gates.length) return null;
  const source = rows(request.candidates).map((item, index) => ({item, index,
    priority: requireInteger(item.priority_rank, "priority_rank"),
    persisted: requireInteger(item.persisted_index, "persisted_index")}));
  const seen = new Set<string>();
  const candidates = source.filter(({item}) => {
    const id = text(item.todo_id);
    if (id && seen.has(id)) return false;
    if (id) seen.add(id);
    const deferred = item.status === "deferred" && item.resume_ready === true;
    return item.archive_state !== "archive" && ((item.status === "open" && item.done !== true) || deferred) &&
      item.removed !== true && (!agent || ((!item.claimed_by || item.claimed_by === agent) &&
        (!item.bound_agent || item.bound_agent === agent) &&
        !(Array.isArray(item.excluded_agents) && item.excluded_agents.includes(agent))));
  });
  candidates.sort((a, b) => a.priority - b.priority ||
    (debt ? Number(a.item.task_class !== "advancement_task") - Number(b.item.task_class !== "advancement_task") : 0) ||
    a.persisted - b.persisted || a.index - b.index);
  const blocked: JsonObject[] = [];
  let selected: typeof candidates[number] | undefined;
  let blockingGate: typeof gates[number] | undefined;
  for (const candidate of candidates) {
    const match = gates.map(g => ({...g, relation: fallbackGateRelation(g.gate, candidate.item)}))
      .find(g => g.relation.state !== "independent");
    if (match) {
      blockingGate ??= match;
      blocked.push({candidate_index: candidate.index, gate_index: match.index, relation: match.relation});
    } else if (!requested || candidate.item.todo_id === requested) selected ??= candidate;
  }
  if (!selected || (!blockingGate && !allowUnrelated)) return null;
  const surface = blockingGate ?? gates[0]!;
  return {schema_version: "scoped_gate_fallback_selection_v0",
    selected_index: selected.index, gate_index: surface.index, blocked: blocked.slice(0, 3), blocked_count: blocked.length,
    has_blocking_gate: blockingGate !== undefined,
    selected_relation: fallbackGateRelation(surface.gate, selected.item),
    deferred_replan: selected.item.status === "deferred" && selected.item.resume_ready === true};
}

function open(items: JsonObject[]): JsonObject[] {
  return items.filter(item => item.done !== true && ["open", "blocked"].includes(String(item.status || "open")));
}
function scopeId(scope: JsonObject): string { return `${scope.kind}:${scope.granularity}:${scope.scope_key}`; }

type ScopedRow = {row: JsonObject; scope: ValidDecisionScope};
type ScopeIndex = Map<string, ScopedRow[]> | JsonObject[];

function parseDecisionScope(value: unknown): ValidDecisionScope | null {
  return object(value) && validDecisionScope(value) ? value : null;
}

function indexDecisionScopes(source: JsonObject[]): ScopeIndex {
  const byKind = new Map<string, ScopedRow[]>();
  for (const row of source) {
    let scope: ValidDecisionScope | null;
    try {
      scope = parseDecisionScope(row.decision_scope);
    } catch (error) {
      if (!(error instanceof TypeError || error instanceof RangeError)) throw error;
      // Keep coercion errors in source order, including when an earlier approval short-circuits.
      return source;
    }
    if (!scope) continue;
    const kind = String(scope.kind);
    const bucket = byKind.get(kind);
    if (bucket) bucket.push({row, scope});
    else byKind.set(kind, [{row, scope}]);
  }
  return byKind;
}

type RequiredScope = {raw: JsonObject; parsed?: ValidDecisionScope | null};

function* coveringRows(index: ScopeIndex, required: RequiredScope): Generator<JsonObject> {
  if (Array.isArray(index)) {
    for (const row of index) if (decisionScopeCovers(row.decision_scope, required.raw)) yield row;
    return;
  }
  if (!index.size) return;
  if (required.parsed === undefined) required.parsed = parseDecisionScope(required.raw);
  const scope = required.parsed;
  if (!scope) return;
  for (const {row, scope: candidate} of index.get(String(scope.kind)) ?? []) {
    if (validScopeCovers(candidate, scope)) yield row;
  }
}

export function decisionScopeConsistency(request: JsonObject): JsonObject {
  const agent = optionalNonEmptyString(request.agent_id, "agent_id");
  const agents = rows(request.agent_items), users = open(rows(request.user_items));
  const items = open(agents), gates = users.filter(item => item.is_gate === true), actions = users.filter(item => item.is_gate !== true);
  if (!Array.isArray(request.registered_agents) || request.registered_agents.some(id => typeof id !== "string")) {
    throw new TypeError("registered agents must be normalized strings");
  }
  const registered = [...new Set(request.registered_agents as string[])].sort();
  const errors: JsonObject[] = [];
  if (registered.length > 1) for (const gate of gates) {
    if (gate.global_gate !== true && !gate.blocks_agent) errors.push({reason_code: "multi_agent_user_gate_missing_scope",
      user_todo_id: gate.todo_id ?? null, registered_agent_ids: registered});
  }
  let gateIndex: ScopeIndex | undefined, actionIndex: ScopeIndex | undefined;
  const standingByOwner = new Map<string | null, {activeEntries: ScopeIndex; conflicts: ScopeIndex}>();
  let checked = 0, terminals = 0, approvals = 0;
  for (const item of items) {
    const claim = text(item.claimed_by);
    if (claim && agent && claim !== agent) continue;
    const owner = agent || claim;
    let standing = standingByOwner.get(owner);
    if (!standing) {
      const authority = scopeStandingAuthority(request.standing_authority, owner);
      standing = {
        activeEntries: indexDecisionScopes(authority?.entries.filter(entry => entry.active === true) ?? []),
        conflicts: indexDecisionScopes(authority?.conflicts ?? []),
      };
      standingByOwner.set(owner, standing);
    }
    for (const scope of rows(item.required_decision_scopes ?? [])) {
      checked++;
      const required: RequiredScope = {raw: scope};
      gateIndex ??= indexDecisionScopes(gates);
      const matching = [...coveringRows(gateIndex, required)];
      const compatible = matching.filter(gate => addressed(gate, owner));
      // These gates already cover this requirement, so a different exact target is a conflict.
      const conflicting = compatible.filter(gate => exactTodoGateRelation(gate, item)?.state === "independent");
      if (conflicting.length) {
        errors.push({reason_code: "required_decision_scope_target_mismatch", agent_todo_id: item.todo_id ?? null,
          required_scope: scopeId(scope), related_user_todo_ids: conflicting.map(g => g.todo_id).filter(Boolean)});
        continue;
      }
      if (compatible.length) continue;
      if (!coveringRows(standing.activeEntries, required).next().done) {
        approvals++; continue;
      }
      const outcomes = rows(item.decision_scope_outcomes ?? []).filter(outcome => ["reject", "cancel"].includes(String(outcome.outcome)) &&
        decisionScopeCovers(outcome.decision_scope, scope));
      if (outcomes.length) {
        terminals++;
        if ((item.status || "open") !== "blocked") errors.push({reason_code: "terminal_decision_outcome_target_not_blocked",
          agent_todo_id: item.todo_id ?? null, required_scope: scopeId(scope), related_user_todo_ids: outcomes.map(o => o.source_todo_id)});
        continue;
      }
      const conflicts = [...coveringRows(standing.conflicts, required)];
      actionIndex ??= indexDecisionScopes(actions);
      const matchingActions = [...coveringRows(actionIndex, required)];
      let reason = "dangling_required_decision_scope", related: unknown[] = [];
      if (conflicts.length) {
        reason = "standing_decision_order_unresolved";
        related = [...new Set(conflicts.flatMap(c => Array.isArray(c.source_todo_ids) ? c.source_todo_ids.filter(id => typeof id === "string") : []))].sort();
      } else if (matchingActions.length) {
        reason = "non_blocking_user_action_scope_collision"; related = matchingActions.map(a => a.todo_id).filter(Boolean);
      } else if (matching.length) {
        reason = "required_decision_scope_gate_owner_mismatch"; related = matching.map(g => g.todo_id).filter(Boolean);
      }
      errors.push({reason_code: reason, agent_todo_id: item.todo_id ?? null, required_scope: scopeId(scope),
        related_user_todo_ids: related as string[]});
    }
  }
  return {schema_version: "required_decision_scope_consistency_v0", ok: !errors.length,
    status: errors.length ? "projection_repair_required" : "consistent", agent_id: agent,
    checked_agent_todo_count: items.length, checked_required_scope_count: checked,
    terminal_outcome_count: terminals, standing_authority_match_count: approvals, errors};
}

function projectDecisionScopeRepairPlan(reasonCodes: readonly string[]): JsonObject {
  const common = {schema_version: "todo_decision_scope_repair_plan_v0", allowed: true, notify: "DONT_NOTIFY"};
  if (reasonCodes.includes("multi_agent_user_gate_missing_scope")) return {
    ...common,
    trigger: "user_gate_scope_projection_drift",
    recommended_mode: "repair_user_gate_scope_projection",
    blocked_action_scope: "todo_user_gate_scope_projection",
    reason: "a multi-agent user_gate lacks blocks_agent or explicit global_gate=true, so its blocking authority is ambiguous",
    repair_focus: "set blocks_agent to one registered agent, set global_gate=true for " +
      "intentional goal-wide authority, or downgrade the item to user_action",
    spend_policy: "spend once only after the gate-scope projection repair is validated and written back",
  };

  const requiredScope = {
    ...common,
    trigger: "required_decision_scope_projection_drift",
    recommended_mode: "repair_required_decision_scope_projection",
    blocked_action_scope: "todo_decision_scope_projection",
    spend_policy: "spend once only after the todo/gate projection repair is validated and written back",
  };
  if (reasonCodes.includes("standing_decision_order_unresolved")) return {
    ...requiredScope,
    reason: "contradictory standing decisions have no established chronological winner",
    repair_focus: "reconcile decision chronology against explicit owner evidence; do not remove " +
      "required_decision_scopes or invent approval merely to clear this conflict",
  };
  if (reasonCodes.includes("required_decision_scope_target_mismatch")) return {
    ...requiredScope,
    reason: "a scope-covering user gate explicitly targets a different Todo",
    repair_focus: "reconcile the exact target and required scope against owner intent; do not " +
      "remove the requirement, retarget the gate, or invent approval merely to clear the conflict",
  };
  return {
    ...requiredScope,
    reason: "an agent todo requires a decision scope that does not resolve to a compatible open user_gate",
    repair_focus: "remove stale required_decision_scopes, create the explicit blocking " +
      "user_gate, or correct its agent ownership; user_action remains non-blocking",
  };
}

function consistencyRepairPlan(consistency: JsonObject): JsonObject | null {
  if (consistency.ok !== false) return null;
  const errors = Array.isArray(consistency.errors) ? consistency.errors : [];
  return projectDecisionScopeRepairPlan(errors.flatMap(error =>
    object(error) && typeof error.reason_code === "string" ? [error.reason_code] : []));
}

type TodoGateScopeState = "clear" | "independent" | "blocked";

/** Dependency projection only; no approval, execution or lease is granted. */
export function projectTodoGateScopes(request: JsonObject): JsonObject {
  const agent = optionalNonEmptyString(request.agent_id, "agent_id");
  const gates = rows(request.gates).filter(gate => gate.is_gate === true &&
    gate.done !== true && ["open", "blocked"].includes(String(gate.status)) &&
    gate.archive_state !== "archive" && addressed(gate, agent));
  return {schema_version: "todo_gate_scope_projections_v0", items: rows(request.items).map(item => {
    const relations = gates.map(gate => fallbackGateRelation(gate, item));
    const state: TodoGateScopeState = !gates.length ? "clear" :
      relations.every(relation => relation.state === "independent") ? "independent" : "blocked";
    return {schema_version: "todo_gate_scope_projection_v0", agent_id: agent,
      todo_id: item.todo_id ?? null, gate_count: gates.length, state};
  })};
}

export function evaluateDecisionScope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "decision scope request");
  if (request.schema_version !== DECISION_SCOPE_REQUEST_SCHEMA) throw new TypeError("decision scope request schema mismatch");
  let result: JsonObject | null | (JsonObject | null)[][];
  let repairPlan: JsonObject | null | undefined;
  switch (request.operation) {
    case "fallback": result = selectScopedGateFallback(request); break;
    case "gate_scopes": result = projectTodoGateScopes(request); break;
    case "consistency": {
      result = decisionScopeConsistency(request);
      repairPlan = consistencyRepairPlan(result) ?? undefined;
      break;
    }
    case "repair_hint": {
      const {consistency, reason_codes} = request;
      result = null;
      if (object(consistency)) repairPlan = consistencyRepairPlan(consistency);
      else if (consistency === undefined && Array.isArray(reason_codes)) {
        repairPlan = projectDecisionScopeRepairPlan(reason_codes.filter(code => typeof code === "string"));
      } else repairPlan = null;
      break;
    }
    case "standing": result = scopeStandingAuthority(request.authority, optionalNonEmptyString(request.agent_id, "agent_id")); break;
    case "relations": {
      const items = rows(request.items);
      result = rows(request.gates).map(gate => items.map(item => todoGateRelation(gate, item)));
      break;
    }
    default: throw new EffectRuntimeRequestError("unsupported decision scope operation");
  }
  return {schema_version: "todo_decision_scope_result_v0", result,
    ...(repairPlan === undefined ? {} : {repair_plan: repairPlan})};
}

/** One complete readiness projection; normal_delivery_allowed is a read signal,
 * never execution permission. Persisted-field codecs remain at the host edge. */
export function projectGlobalTodoReadiness(value: unknown): JsonObject {
  const request = requireJsonObject(value, "global readiness request");
  if (request.schema_version !== "global_todo_readiness_request_v0") {
    throw new TypeError("global readiness request schema mismatch");
  }
  const payload = requireJsonObject(request.payload, "payload");
  const decoded = requireJsonObject(request.decoded, "decoded");
  const gates = rows(request.gates);
  const candidates: JsonObject[] = [], warnings: JsonObject[] = [];
  const records = new Map<string, {
    item: JsonObject; facts: JsonObject; sources: Set<string>; statuses: Set<string>;
  }>();
  const add = (source: string, value: unknown, path: string): void => {
    if (!object(value)) return;
    const facts = requireJsonObject(decoded[path], `decoded ${path}`);
    const todoId = readinessCodecText(facts, "identity_text");
    if (!todoId) {
      warnings.push({reason_code: "todo_identity_missing",
        detail: `Structured todo candidate from ${source} has no todo_id.`});
      return;
    }
    let record = records.get(todoId);
    if (!record) {
      record = {item: {...value}, facts: {...facts}, sources: new Set(), statuses: new Set()};
      records.set(todoId, record);
    }
    record.sources.add(source);
    const status = readinessCodecText(facts, "status_text").toLowerCase();
    if (status) record.statuses.add(status);
    for (const [key, field] of Object.entries(value)) {
      if (readinessMissing(record.item[key]) && !readinessMissing(field)) {
        record.item[key] = field;
        if (Object.hasOwn(facts, key)) record.facts[key] = facts[key];
      }
    }
  };
  add("selected_todo", payload.selected_todo, "/selected_todo");
  add("agent_lane_next_action", payload.agent_lane_next_action, "/agent_lane_next_action");
  for (const containerKey of ["agent_todo_summary", "agent_scope_frontier"]) {
    const container = object(payload[containerKey]) ? payload[containerKey] : {};
    for (const key of ["first_open_items", "first_executable_items", "items", "open_items", "deferred_items",
      "deferred_resume_candidates", "blocker_items", "resume_blocked_items"]) {
      const values = container[key];
      if (Array.isArray(values)) values.forEach((item, index) =>
        add(`${containerKey}.${key}`, item, `/${containerKey}/${key}/${index}`));
    }
  }
  const selected = object(payload.selected_todo)
    ? readinessCodecText(requireJsonObject(decoded["/selected_todo"], "selected codec"), "identity_text") : "";
  for (const [todoId, record] of records) {
    const sources = [...record.sources];
    const blocked = sources.some(source =>
      ["blocker_items", "resume_blocked_items"].includes(source.split(".").at(-1)!)) ||
      gates.some(gate => {
        const relation = todoGateRelation(gate, record.facts);
        return readinessCodecText(gate, "target_text") === todoId ||
          relation?.state === "gate_targets_todo" || relation?.state === "gate_covers_action";
      }) || record.statuses.has("blocked");
    const deferred = sources.some(source => source.endsWith(".deferred_resume_candidates")) ||
      (record.statuses.has("deferred") && record.item.resume_ready === true);
    const runnable = todoId === selected && payload.normal_delivery_allowed === true;
    if (!blocked && !deferred && !runnable) continue;
    if (Number(blocked) + Number(deferred) + Number(runnable) > 1) warnings.push({
      reason_code: "conflicting_readiness_projection", todo_id: todoId,
      detail: "Multiple structured readiness signals were projected.",
    });
    candidates.push({item: record.item, readiness: blocked ? "blocked" : deferred ? "deferred_ready" : "runnable"});
  }
  return {schema_version: "global_todo_readiness_v0", candidates, warnings};
}

// Python's merge sentinel is equality with (None, "", []), not truthiness:
// false, zero and an empty object must survive the first-source merge.
function readinessMissing(value: unknown): boolean {
  return value == null || value === "" || (Array.isArray(value) && !value.length);
}

// Preserve legacy scalar spelling (including Python booleans and floats) at
// the codec edge; traversal, whitespace normalization and decisions stay here.
function readinessCodecText(facts: JsonObject, key: string): string {
  const value = facts[key];
  if (typeof value !== "string") throw new TypeError(`global readiness codec ${key} must be a string`);
  return stripPythonWhitespace(value);
}
