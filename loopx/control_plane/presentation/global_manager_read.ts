/** Complete global read projections; host adapters retain incremental source IO. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireInteger, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {sealProjectionEnvelope} from "../projection_envelope.ts";
import {todoGateRelation} from "../todos/decision_scope.ts";
import {object, id, readScope, readScopes, pythonFieldText} from "../todos/read_codec.ts";
import {authorityUnicodeCompare} from "../coordination/authority_store_codec.ts";
const COMMAND = "/loopx-global-summary";
const GLOBAL_GATES_COMMAND = "/loopx-global-gates";
const SCHEMA_VERSION = "global_manager_command_response_v0";
const GLOBAL_GATES_SOURCE_SURFACES = ["status attention queue", "quota should-run summaries", "active-state todo projections", "compact run-history projection consumed by status"];
const LANE_PRIORITY: Record<string, number> = {"eligible": 0, "normal_run": 0, "run": 0, "operator_gate": 1, "focus_wait": 1, "waiting": 2, "throttled": 3, "paused": 4, "quota_unavailable": 5};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const rows = (value: unknown): JsonObject[] => list(value).filter(value => value !== null && typeof value === "object" && !Array.isArray(value)).map(object);
const truth = (value: unknown): boolean => Array.isArray(value) ? value.length > 0
  : value !== null && typeof value === "object" ? Object.keys(value).length > 0 : Boolean(value);
const or = (...values: unknown[]): unknown => values.find(truth) ?? values.at(-1);
const str = (value: unknown): string => pythonFieldText({value: value ?? ""}, "value");
const field = (row: JsonObject, key: string): string => str(or(row[key], ""));
const display = (...fields: [JsonObject, string][]): string => {
  const selected = fields.find(([row, key]) => truth(row[key]));
  return selected ? String(object(selected[0]._display)[selected[1]] ?? "") : "";
};
const firstTodo = (quota: JsonObject): JsonObject | null => {
  const lane = object(quota.agent_lane_next_action);
  if (Object.keys(lane).length) return lane;
  const summary = object(quota.agent_todo_summary);
  for (const key of ["first_open_items", "items", "open_items"]) {
    const item = rows(summary[key])[0];
    if (item) return item;
  }
  return null;
};
function laneFrom(item: JsonObject, quota: JsonObject): JsonObject {
  const lane = object(quota.agent_lane_next_action), todo = firstTodo(quota);
  const asset = object(item.project_asset);
  const result: JsonObject = {
    goal_id: str(or(item.goal_id, quota.goal_id, "")),
    status: str(or(quota.state, item.status, "unknown")),
    waiting_on: str(or(item.waiting_on, quota.waiting_on, "")),
    agent_id: truth(or(lane.agent_id, object(quota.agent_identity).agent_id, object(asset.owner).agent_id))
      ? str(or(lane.agent_id, object(quota.agent_identity).agent_id, object(asset.owner).agent_id)) : null,
    top_todo_id: todo?.todo_id ?? null,
    last_event_id: object(quota.rollout_event).event_id ?? null,
    next_safe_action: display([quota, "recommended_action"], [item, "recommended_action"], [asset, "next_action"]),
  };
  for (const key of Object.keys(result)) if (result[key] === null || result[key] === "") delete result[key];
  return result;
}
const waitAction = "Wait for the projected user/controller decision before running the blocked path.";
function foreignTodoGateOnly(quota: JsonObject): boolean {
  const scope = object(object(quota.user_todo_summary).agent_scope_filter);
  // Scope counts describe Todo gates, not decisions from another or unknown source.
  return quota.source === "active_state" && quota.status === "active_state_user_gate" && !truth(quota.gate_id)
    && typeof scope.agent_id === "string" && scope.current_agent_blocking_open_count === 0
    && typeof scope.other_agent_scoped_open_count === "number" && scope.other_agent_scoped_open_count > 0;
}
function summaryGate(item: JsonObject, quota: JsonObject): JsonObject | null {
  if (foreignTodoGateOnly(quota)) return null;
  const channel = object(object(quota.interaction_contract).user_channel), summary = object(quota.user_todo_summary);
  if (!truth(channel.action_required) && Number(or(summary.open_count, summary.count, 0)) <= 0) return null;
  let first: JsonObject = {};
  for (const key of ["first_open_items", "items", "open_items"]) {
    first = rows(summary[key])[0] ?? {};
    if (Object.keys(first).length) break;
  }
  const goal = str(or(item.goal_id, quota.goal_id, ""));
  return {gate_id: str(or(quota.gate_id, `${goal}:user-gate`)), goal_id: goal, owner: "user",
    blocks: truth(first.todo_id) ? [first.todo_id] : [goal],
    question: display([channel, "question"], [quota, "operator_question"], [quota, "gate_prompt"], [first, "text"], [item, "recommended_action"]),
    next_safe_action: waitAction};
}
function candidates(quota: JsonObject): JsonObject[] {
  const summary = object(quota.agent_todo_summary), seen = new Set<string>(), result: JsonObject[] = [];
  const values = [quota.selected_todo, quota.agent_lane_next_action,
    ...["first_open_items", "first_executable_items", "items", "open_items", "backlog_items", "executable_backlog_items"].flatMap(key => list(summary[key]))];
  for (const item of rows(values)) {
    const key = JSON.stringify([item.todo_id ?? null, item.index ?? null, item.text ?? null]);
    if (!seen.has(key)) {seen.add(key); result.push(item);}
  }
  return result;
}
function blockedRefs(gate: JsonObject | null, quota: JsonObject, goal: string): string[] {
  if (!gate) return [goal];
  const exact = field(gate, "unblocks_todo_id").trim();
  if (exact) return [exact];
  const facts = (row: JsonObject): JsonObject => ({todo_id: id(row.todo_id), unblocks_todo_id: id(row.unblocks_todo_id),
    decision_scope: readScope(row.decision_scope), required_decision_scopes: readScopes(row.required_decision_scopes)});
  for (const item of candidates(quota)) {
    const relation = todoGateRelation(facts(gate), facts(item));
    if (relation && ["gate_targets_todo", "gate_covers_action"].includes(String(relation.state))) {
      const todo = field(item, "todo_id").trim();
      if (todo) return [todo];
    }
  }
  return [goal];
}
function globalGate(item: JsonObject, quota: JsonObject, facts: GateSourceFacts): JsonObject | null {
  if (!facts.eligible) return null;
  // A peer notification can mention another peer's gate. Preserve the explicit
  // scope counts rather than presenting that notification as an additional gate.
  if (!facts.formal_gate && foreignTodoGateOnly(quota)) return null;
  const channel = object(object(quota.interaction_contract).user_channel), formal = facts.formal_gate;
  const action = channel.action_required === true, waiting = field(item, "waiting_on").trim();
  const owner = formal || action ? "user" : "controller", goal = str(or(item.goal_id, quota.goal_id, "")).trim();
  return {gate_id: str(or(quota.gate_id, formal?.todo_id, `${goal}:${owner}-gate`)), goal_id: goal, owner,
    waiting_on: waiting || owner, blocks: blockedRefs(formal, quota, goal),
    question: display([channel, "question"], [item, "operator_question"], [formal ?? {}, "text"], [formal ?? {}, "title"], [quota, "recommended_action"], [item, "recommended_action"]),
    next_safe_action: waitAction};
}
function envelope(input: JsonObject, projection: string, evaluated: number, unavailable: number, shown: number, available: number): JsonObject {
  const status = object(input.status), registry = object(status.global_registry), upstream = object(status.projection_envelope);
  const expected = truth(registry.available) ? Number(or(registry.global_goal_count, 0)) : null;
  const excluded = truth(registry.available) ? Number(or(registry.current_registry_excluded_goal_count, 0)) : 0;
  const refs = truth(registry.available) ? list(registry.current_registry_excluded_goal_ids).map(str) : [];
  const global = rows(upstream.sources).find(row => row.source_id === "global_registry") ?? {};
  const source = (source_id: string, read_status: string, last_read_at: unknown, item_count: unknown = null, unreadable_count = 0): JsonObject =>
    ({source_id, read_status, last_read_at, required: true, source_updated_at: null, item_count, missing_count: 0, unreadable_count});
  return sealProjectionEnvelope({schema_version: "loopx_projection_envelope_seal_request_v0", projection, observed_at: input.observed_at,
    sources: [source("global_registry", str(or(global.read_status, truth(registry.available) ? "read" : "not_read")),
      or(global.last_read_at, truth(registry.available) ? input.quota_read_at : null) ?? null, expected),
    source("goal_quota", input.quota_read_at ? "read" : "not_read", input.quota_read_at, evaluated, unavailable),
    ...(Object.keys(upstream).length ? [] : [source("status", "not_read", null)])],
    coverage: {scope: "global", expected_count: expected,
      included_count: expected !== null ? Math.max(0, expected - excluded) : Number(or(object(upstream.coverage).included_count, 0)),
      omitted: excluded ? [{reason: "outside_current_registry", count: excluded, refs: refs.slice(0, 8)}] : [], shown_count: shown, available_count: available},
    upstream: Object.keys(upstream).length ? [upstream] : []});
}
const queueCount = (status: JsonObject): number => new Set(rows(object(status.attention_queue).items).map(row => field(row, "goal_id").trim()).filter(Boolean)).size;
const gateKey = (gate: JsonObject): string => JSON.stringify([gate.goal_id, gate.gate_id, gate.question, gate.blocks]);
type GateSourceFacts = {formal_gate: JsonObject | null; eligible: boolean};
function decodeGateSourceFacts(value: unknown): GateSourceFacts {
  const facts = requireJsonObject(value, "gate_source_facts");
  if (typeof facts.eligible !== "boolean") throw new Error("gate_source_facts.eligible must be a boolean");
  return {eligible: facts.eligible, formal_gate: facts.formal_gate === null ? null
    : requireJsonObject(facts.formal_gate, "gate_source_facts.formal_gate")};
}
function decode(input: JsonObject): {status: JsonObject; samples: {item: JsonObject; quota: JsonObject; gate_source_facts: unknown}[]; limit: number} {
  if (input.schema_version !== "global_manager_read_request_v0") throw new Error("Unsupported global manager read schema");
  const status = requireJsonObject(input.status, "status"), limit = requireInteger(input.limit, "limit");
  requireNonEmptyString(input.observed_at, "observed_at");
  requireNonEmptyString(input.generated_at, "generated_at");
  requireJsonObject(input.boundary, "boundary");
  if (!Array.isArray(input.samples)) throw new Error("samples must be an array");
  const samples = input.samples.map(value => {
    const row = requireJsonObject(value, "sample");
    return {item: requireJsonObject(row.item, "item"), quota: requireJsonObject(row.quota, "quota"), gate_source_facts: row.gate_source_facts};
  });
  return {status, limit, samples};
}
const action = (action_id: string, kind: string, preview: string): JsonObject =>
  ({action_id, kind, requires_user_approval: false, requires_maintainer_authority: false, preview});
const omission = "Raw logs, raw transcripts, connector payloads, credential values, local paths, and private source bodies were intentionally omitted.";
export function projectGlobalSummary(input: JsonObject): JsonObject {
  const {status, samples, limit} = decode(input);
  requireNonEmptyString(input.time_range, "time_range");
  const surfaces = requireStringArray(input.source_surfaces, "source_surfaces");
  if (input.since !== null) requireInteger(input.since, "since");
  const lanes: JsonObject[] = [], gates: JsonObject[] = [], todos: JsonObject[] = [], states: Record<string, number> = {};
  const goals = new Map<string, typeof samples>();
  for (const sample of samples) {
    const goal = field(sample.item, "goal_id").trim(), group = goals.get(goal) ?? [];
    group.push(sample); goals.set(goal, group);
  }
  for (const [goal, group] of goals) {
    // Quota owns eligibility. Retain one goal lane while reading every peer's
    // gates and work, including peers after a runnable recommendation.
    const preferred = group.find(({quota}) => quota.normal_delivery_allowed === true)
      ?? group.find(({quota}) => quota.ok !== false && Object.keys(object(quota.selected_todo)).length > 0)
      ?? group.find(({quota}) => quota.ok !== false) ?? group[0]!;
    const lane = laneFrom(preferred.item, preferred.quota);
    lanes.push(lane); states[String(lane.status ?? "unknown")] = (states[String(lane.status ?? "unknown")] ?? 0) + 1;
    const seenGates = new Set<string>(), seenTodos = new Set<string>();
    for (const {item, quota, gate_source_facts} of group) {
      const gate = gate_source_facts === undefined ? summaryGate(item, quota)
        : globalGate(item, quota, decodeGateSourceFacts(gate_source_facts));
      const todo = firstTodo(quota);
      if (gate && !seenGates.has(gateKey(gate))) {seenGates.add(gateKey(gate)); gates.push(gate);}
      if (todo) {
        const key = str(or(todo.todo_id, todo.title, ""));
        if (seenTodos.has(key)) continue;
        seenTodos.add(key);
        todos.push({todo_id: todo.todo_id ?? null, goal_id: goal, role: or(todo.role, "agent"), claimed_by: todo.claimed_by ?? null,
          state: or(todo.status, "open"), priority: todo.priority ?? null, title: display([todo, "title"], [todo, "text"]), next_safe_action: display([quota, "recommended_action"])});
      }
    }
  }
  lanes.sort((a, b) => (LANE_PRIORITY[String(a.status)] ?? 4) - (LANE_PRIORITY[String(b.status)] ?? 4) || authorityUnicodeCompare(field(a, "goal_id"), field(b, "goal_id")));
  const unavailable = [...goals.values()].filter(group => group.some(({quota}) => quota.state === "quota_unavailable")).length;
  const sealed = envelope(input, "global_summary", goals.size, unavailable,
    Math.min(lanes.length, limit), queueCount(status));
  const risks = rows(object(status.global_registry).findings).map(row => ({kind: str(or(row.kind, "status_warning")), severity: str(or(row.severity, "info")),
    goal_id: row.goal_id ?? null, evidence_refs: ["status_contract"], next_safe_action: display([row, "recommended_action"], [row, "message"])}));
  const progress: JsonObject[] = [];
  for (const row of rows(object(status.run_history).recent_runs)) {
    if (input.since !== null && row._parsed_at !== null && row._parsed_at !== undefined) {
      if (row._naive_time) throw new Error("can't compare offset-naive and offset-aware datetimes");
      if (Number(row._parsed_at) < Number(input.since)) continue;
    }
    progress.push({goal_id: row.goal_id ?? null, generated_at: row.generated_at ?? null,
      classification: object(row._display).classification ?? "", recommended_action: row._progress_action ?? ""});
    if (progress.length >= limit) break;
  }
  const runnable = lanes.filter(row => ["eligible", "normal_run", "run"].includes(String(row.status)));
  const waiting = (row: JsonObject) => ["controller", "user", "external"].includes(field(row, "waiting_on"));
  const slice = <T>(values: T[]): T[] => values.slice(0, limit < 0 ? Math.max(0, values.length + limit) : limit);
  return {ok: true, schema_version: SCHEMA_VERSION,
    request: {schema_version: "global_manager_command_request_v0", command: COMMAND, legacy_aliases: ["/loop-global-summary"], cli_command: "loopx global-summary",
      time_range: input.time_range, include: ["progress", "gates", "todos", "risks", "next_actions"], privacy_mode: "public_safe_summary", dry_run: true},
    generated_at: input.generated_at,
    summary: {headline: `${progress.length} recent progress items, ${gates.length} open user/controller gates, ${todos.length} runnable or visible todos.`,
      progress_count: progress.length, open_gate_count: gates.length, runnable_todo_count: runnable.length, waiting_lane_count: lanes.filter(waiting).length,
      risk_count: risks.length, source_surfaces: surfaces, quota_states: states},
    groups: {user_gates: slice(gates), runnable_agent_work: slice(runnable),
      waiting_lanes: slice(lanes.filter(row => waiting(row) || ["waiting", "focus_wait", "operator_gate"].includes(String(row.status)))),
      health_risks: slice(risks), recent_progress: progress},
    lanes: slice(lanes), gates: slice(gates), todos: slice(todos), risks: slice(risks), recent_progress: progress,
    actions: [action("act_read_goal_detail", "read_more", "Run `loopx review-packet --goal-id <goal>` or `loopx quota should-run --goal-id <goal>` for one lane."),
      action("act_ask_user_gate", "ask_user", "Surface the projected gate question when open_gate_count is non-zero.")],
    omissions: [omission, "Status health findings are summarized without filesystem paths."], projection_envelope: sealed, boundary: input.boundary};
}
export function projectGlobalGates(input: JsonObject): JsonObject {
  const {status, samples, limit} = decode(input);
  const gateSamples = samples.map(sample => ({...sample, facts: decodeGateSourceFacts(sample.gate_source_facts)}));
  const request = {schema_version: "global_manager_command_request_v0", command: GLOBAL_GATES_COMMAND, legacy_aliases: ["/loop-global-gates"],
    cli_command: "loopx global-gates", include: ["gates", "blocked_work", "next_questions"], privacy_mode: "public_safe_summary", dry_run: true};
  if (status.ok !== true) return {ok: false, schema_version: SCHEMA_VERSION, request, generated_at: input.generated_at,
    error: "Global status source unavailable.", omissions: ["Raw/private failure details and local paths were intentionally omitted."], boundary: input.boundary,
    projection_envelope: envelope(input, "global_gates", 0, 0, 0, 0)};
  const gates: JsonObject[] = [], lanes: JsonObject[] = [], seenGates = new Set<string>();
  const agent = input.agent_id == null ? null : requireNonEmptyString(input.agent_id, "agent_id");
  for (const {item, quota, facts} of gateSamples) {
    if (agent && (quota.ok === false || field(object(quota.agent_identity), "agent_id") !== agent)) continue;
    const gate = globalGate(item, quota, facts);
    if (!gate) continue;
    if (seenGates.has(gateKey(gate))) continue;
    seenGates.add(gateKey(gate));
    gates.push(gate);
    const lane = laneFrom(item, quota), goal = field(item, "goal_id").trim();
    const verified = list(gate.blocks).map(str).filter(ref => ref !== goal);
    if (!verified.includes(field(lane, "top_todo_id"))) delete lane.top_todo_id;
    lanes.push(lane);
    if (gates.length >= limit) break;
  }
  const evaluated = new Set(samples.map(({item}) => field(item, "goal_id").trim())).size;
  const unavailable = new Set(samples.filter(({quota}) => quota.state === "quota_unavailable").map(({item}) => field(item, "goal_id").trim())).size;
  const sealed = envelope(input, "global_gates", evaluated, unavailable, evaluated, queueCount(status));
  return {ok: true, schema_version: SCHEMA_VERSION, request, generated_at: input.generated_at,
    summary: {headline: `${gates.length} open user/controller ${gates.length === 1 ? "gate requires" : "gates require"} a decision.`,
      open_gate_count: gates.length, blocked_lane_count: lanes.length, source_surfaces: GLOBAL_GATES_SOURCE_SURFACES},
    groups: {user_gates: gates.filter(row => row.owner === "user"), controller_gates: gates.filter(row => row.owner === "controller"), blocked_work: lanes},
    gates, lanes, actions: gates.length ? [action("act_read_gate_detail", "read_more", "Run `loopx quota should-run --goal-id <goal>` for one blocked lane."),
      action("act_ask_gate_owner", "ask_user", "Surface the projected gate question without changing state.")] : [],
    omissions: [omission, "Recent progress and unrelated risks are outside this focused command."], projection_envelope: sealed, boundary: input.boundary};
}
