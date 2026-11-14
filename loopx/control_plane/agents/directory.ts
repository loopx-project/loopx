/** Peer directory decision owner. IO, public-safety filtering and management
 * projection stay with their existing adapters; this projection reads no clock. */
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireJsonObject, requireNonEmptyString, requireStringArray,
  optionalNonEmptyString } from "../runtime_decode.ts";
import { sealProjectionEnvelope, PROJECTION_ENVELOPE_SEAL_REQUEST } from "../projection_envelope.ts";

type ObjectRow = Record<string, unknown>;
const object = (value: unknown): ObjectRow => jsonObject(value) ?? {};
const text = (value: unknown): string | null => typeof value === "string" && value.length ? value : null;
const integer = (value: unknown): number | null => typeof value === "number" && Number.isInteger(value) ? value : null;
function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value;
}
function validateAgent(row: ObjectRow, index: number): ObjectRow {
  const label = `agents[${index}]`;
  const strings = (input: ObjectRow, keys: string[], prefix: string) => {
    for (const key of keys) {
      const value = input[key];
      if (value !== undefined && value !== null && typeof value !== "string") {
        throw new EffectRuntimeRequestError(`${prefix}.${key} must be a string or null`);
      }
    }
  };
  strings(row, ["agent_id", "agent_model", "execution_fact_agent_id", "last_activity_at"], label);
  for (const key of ["evidence_refs", "handoff_refs"]) {
    if (row[key] !== undefined) requireStringArray(row[key], `${label}.${key}`);
  }
  if (row.current_todo !== undefined) strings(requireJsonObject(row.current_todo, `${label}.current_todo`),
    ["todo_id", "status", "task_class", "action_kind", "priority", "claimed_by", "updated_at", "title"], `${label}.current_todo`);
  if (row.stale_claim_hint !== undefined) strings(requireJsonObject(row.stale_claim_hint, `${label}.stale_claim_hint`), ["state"], `${label}.stale_claim_hint`);
  if (row.execution !== undefined) {
    const execution = requireJsonObject(row.execution, `${label}.execution`);
    if (execution.lease !== undefined) {
      const lease = requireJsonObject(execution.lease, `${label}.execution.lease`);
      strings(lease, ["status"], `${label}.execution.lease`);
      if (lease.expired !== undefined && lease.expired !== null && typeof lease.expired !== "boolean") {
        throw new EffectRuntimeRequestError(`${label}.execution.lease.expired must be a boolean or null`);
      }
    }
  }
  return row;
}
function refs(value: unknown): string[] {
  return [...new Set(Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && !!v) : [])].slice(0, 1);
}
function workBlock(row: ObjectRow): ObjectRow | null {
  const todo = object(row.current_todo), hint = object(row.stale_claim_hint);
  if (!text(todo.todo_id)) return null;
  const claimedBy = text(todo.claimed_by), lease = object(object(row.execution).lease);
  const work: ObjectRow = {
    todo_id: todo.todo_id, todo_status: text(todo.status) ?? "unknown",
    lease_status: text(lease.status), lease_expired: typeof lease.expired === "boolean" ? lease.expired : null,
    task_class: text(todo.task_class), action_kind: text(todo.action_kind), priority: text(todo.priority),
    claimed: !!claimedBy, claimed_by: claimedBy,
    claim_age_state: hint.state === "suspected_stale" ? "suspected_stale" : claimedBy && text(todo.updated_at) ? "fresh" : "unknown",
    last_activity_at: text(row.last_activity_at), title: text(todo.title),
  };
  return Object.fromEntries(Object.entries(work).filter(([, value]) => value !== null && value !== ""));
}
function rollup(rows: ObjectRow[]): ObjectRow {
  const needs = rows.filter(row => object(row.work).todo_status === "blocked").map(row => row.agent_id);
  const stale = rows.filter(row => object(row.work).claim_age_state === "suspected_stale").map(row => row.agent_id);
  const unclaimed = rows.filter(row => !object(row.work).claimed).map(row => row.agent_id);
  return {basis: "typed_work_state_only", assigns_work: false, needs_decision: needs.slice(0, 8),
    stale_claims: stale.slice(0, 8), without_claim: unclaimed.slice(0, 8),
    counts: {needs_decision: needs.length, stale_claims: stale.length, without_claim: unclaimed.length}};
}

export function projectPeerAgentDirectory(value: unknown): ObjectRow {
  const request = requireJsonObject(value, "peer directory request");
  if (request.schema_version !== "peer_agent_directory_project_request_v0") {
    throw new EffectRuntimeRequestError("peer directory request schema mismatch");
  }
  const now = requireNonEmptyString(request.observed_at, "observed_at");
  const caller = optionalNonEmptyString(request.caller_agent_id, "caller_agent_id");
  const goal = optionalNonEmptyString(request.goal_id, "goal_id");
  const cursor = optionalNonEmptyString(request.cursor, "cursor");
  const registered = new Set(requireStringArray(request.registered_agent_ids, "registered_agent_ids"));
  const agents = array(request.agents, "agents").map((row, index) => validateAgent(requireJsonObject(row, `agents[${index}]`), index));
  const summary = requireJsonObject(request.source_summary, "source_summary");
  const routes = requireJsonObject(request.routes, "routes");
  const executionIds = request.execution_fact_agent_ids === null ? null
    : new Set(requireStringArray(request.execution_fact_agent_ids, "execution_fact_agent_ids"));
  const upstream = array(request.upstream_envelopes, "upstream_envelopes");
  // Validate the route facts before projecting so malformed requests cannot be
  // silently accepted just because the caller or display cap hides a row.
  for (const [id, value] of Object.entries(routes)) {
    const route = requireJsonObject(value, `routes.${id}`);
    array(route.candidates, `routes.${id}.candidates`).forEach((candidate, index) => requireJsonObject(candidate, `routes.${id}.candidates[${index}]`));
    if (route.withheld_candidate_count !== undefined && (integer(route.withheld_candidate_count) === null || Number(route.withheld_candidate_count) < 0)) {
      throw new EffectRuntimeRequestError(`routes.${id}.withheld_candidate_count must be a non-negative integer`);
    }
  }
  const limitations = ["presence_provider_unavailable", "presence_is_advisory"];
  if (executionIds === null || agents.some(row => !executionIds.has(String(row.execution_fact_agent_id ?? row.agent_id ?? "").trim()))) {
    limitations.push("lease_state_not_projected");
  }
  const gaps: ObjectRow[] = caller && !registered.has(caller) ? [{kind: "audience_not_authorized",
    detail: "caller_agent_id_is_not_registered_for_this_goal", caller_agent_id: caller}] : [];
  if (!caller) limitations.push("caller_identity_not_supplied");
  const rows: ObjectRow[] = [];
  let withheld = 0;
  for (const row of agents) {
    const id = text(row.agent_id);
    if (!id) continue;
    if (gaps.length || rows.length >= 24) break;
    const route = requireJsonObject(routes[id], `routes.${id}`);
    withheld += Number(route.withheld_candidate_count ?? 0);
    const projected: ObjectRow = {agent_id: id, registered: registered.has(id), work: workBlock(row),
      peer_route: {...route, candidates: (route.candidates as unknown[]).slice(0, 3)}};
    if (text(row.agent_model)) projected.agent_model = row.agent_model;
    const observations = refs(row.evidence_refs), deliveries = refs(row.handoff_refs);
    if (observations.length) projected.observation_refs = observations;
    if (deliveries.length) projected.delivery_refs = deliveries;
    rows.push(projected);
  }
  const diagnostic = integer(summary.diagnostic_agent_count) ?? 0;
  const sourceCount = registered.size + diagnostic;
  const omitted = gaps.length ? 0 : Math.max(0, sourceCount - rows.length);
  if (omitted) limitations.push("rows_truncated_at_cap");
  if (withheld) limitations.push("route_candidate_withheld");
  const selected = integer(summary.selected_agent_count);
  const more = rows.length > 0 && !gaps.length && (selected !== null ? selected > rows.length : cursor === null && sourceCount > rows.length);
  const packet: ObjectRow = {ok: true, schema_version: "peer_agent_directory_v0", goal_id: goal, collected_at: now,
    scope: {mode: "goal_registered_agents", caller_agent_id: caller,
      caller_membership: caller ? registered.has(caller) ? "registered_agent" : "unregistered_agent" : "local_surface",
      scope_basis: "goal_registry", gaps}, rows, row_count: rows.length, omitted_row_count: omitted,
    registered_agent_count: registered.size, cursor, next_cursor: more ? rows.at(-1)!.agent_id : null,
    presence_coverage: {provider: null, state: "unavailable", note: "presence answers 'runnable now'; no presence provider is registered for this Goal, so rows carry registry identity and durable work state only"},
    authority: {observation_grants: [], writes: false, scheduler: false, note: "observation and delivery grant no claim, lease, priority or work edit"}, limitations};
  if (diagnostic) packet.diagnostic_agent_count = diagnostic;
  if (rows.length) packet.rollup = rollup(rows);
  packet.projection_envelope = sealProjectionEnvelope({schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST,
    projection: "peer_agent_directory", observed_at: now, sources: upstream.length ? [] : [{
      source_id: "directory_inputs", read_status: "not_read", required: true, last_read_at: null}], upstream,
    coverage: {scope: "goal_registered_agents", expected_count: upstream.length ? sourceCount : null,
      included_count: gaps.length ? 0 : sourceCount, shown_count: rows.length, available_count: sourceCount,
      omitted: gaps.length && sourceCount ? [{reason: "audience_not_authorized", count: sourceCount}] : []}});
  return packet;
}
