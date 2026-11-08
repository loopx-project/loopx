/** Read-only Goal routing inside the quota planning batch, never execution authority. */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireInteger, requireJsonObject, requireStringArray } from "../runtime_decode.ts";

export const GOAL_ROUTE_HINT_SCHEMA_VERSION = "goal_route_hint_v0";
const ITEM_LIMIT = 3;

interface RouteRow extends JsonObject {
  done: boolean;
  status: string | null;
  task_class: string;
  todo_id: string | null;
  claimed_by: string | null;
  blocks_agent: string | null;
  unblocks_todo_id: string | null;
  excluded_agents: string[];
  text: string;
  display_text: string;
  gate_identity: string;
  gate_state: unknown;
}
interface CurrentAction extends JsonObject {
  todo_id: string | null;
  text: string;
  claim_required_before_work: boolean;
}
interface Frontier { action: string; effective_action: string; reason: string }
interface TaskRouteOverride extends JsonObject {
  omit_current_agent_next_action?: boolean;
  kind?: string;
  route_decision?: string;
  reason?: string;
  peer_lane_count?: number;
}
export interface GoalRouteContext {
  agent_id: string | null;
  agent_todo_summary: {
    claimed_advancement_open_items: (RouteRow | null)[] | null;
    claimed_by_others_items: (RouteRow | null)[] | null;
    executable_backlog_items: (RouteRow | null)[] | null;
    unclaimed_priority_open_items: (RouteRow | null)[] | null;
    current_agent_handoff_gates: (RouteRow | null)[] | null;
    handoff_gates: (RouteRow | null)[] | null;
    current_agent_claimed_advancement_count: number;
    claimed_advancement_open_count: number;
  };
  agent_lane_next_action: CurrentAction | null;
  agent_scope_frontier: Frontier | null;
  agent_lane_frontier_hint: JsonObject | null;
  active_state_next_action: string;
  selected_recommended_action: string;
  latest_run_recommended_action: string;
  actions_projection_aligned: boolean;
  goal_route_hint_override: TaskRouteOverride | null;
}
function text(value: unknown, label: string): string {
  if (typeof value !== "string") throw new EffectRuntimeRequestError(`${label} must be a string`);
  return value;
}
function nullableText(value: unknown, label: string): string | null {
  return value === null ? null : text(value, label);
}
function count(value: unknown, label: string): number {
  const result = requireInteger(value, label);
  if (result < 0) throw new EffectRuntimeRequestError(`${label} must be nonnegative`);
  return result;
}
function rows(value: unknown, label: string): (RouteRow | null)[] | null {
  if (value === null) return null;
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array or null`);
  return value.map((entry, index) => {
    if (entry === null) return null;
    const path = `${label}[${index}]`, raw = requireJsonObject(entry, path);
    return {
      done: requireBoolean(raw.done, `${path}.done`),
      status: nullableText(raw.status, `${path}.status`),
      task_class: text(raw.task_class, `${path}.task_class`),
      todo_id: nullableText(raw.todo_id, `${path}.todo_id`),
      claimed_by: nullableText(raw.claimed_by, `${path}.claimed_by`),
      blocks_agent: nullableText(raw.blocks_agent, `${path}.blocks_agent`),
      unblocks_todo_id: nullableText(raw.unblocks_todo_id, `${path}.unblocks_todo_id`),
      excluded_agents: requireStringArray(raw.excluded_agents, `${path}.excluded_agents`),
      text: text(raw.text, `${path}.text`),
      display_text: text(raw.display_text, `${path}.display_text`),
      gate_identity: text(raw.gate_identity, `${path}.gate_identity`),
      gate_state: raw.gate_state,
      ...(raw.priority == null ? {} : { priority: raw.priority }),
      ...(raw.action_kind == null ? {} : { action_kind: raw.action_kind }),
    };
  });
}
function override(value: unknown): TaskRouteOverride | null {
  if (value === null) return null;
  const raw = requireJsonObject(value, "goal_route_hint_override");
  const result: TaskRouteOverride = {};
  for (const field of ["kind", "route_decision", "reason"] as const) {
    if (raw[field] !== undefined) result[field] = text(raw[field], `goal_route_hint_override.${field}`);
  }
  if (raw.omit_current_agent_next_action !== undefined) result.omit_current_agent_next_action =
    requireBoolean(raw.omit_current_agent_next_action, "goal_route_hint_override.omit_current_agent_next_action");
  if (raw.peer_lane_count !== undefined) result.peer_lane_count = count(raw.peer_lane_count, "goal_route_hint_override.peer_lane_count");
  for (const field of Object.keys(raw)) {
    if (!Object.hasOwn(result, field)) throw new EffectRuntimeRequestError(`unsupported goal_route_hint_override.${field}`);
  }
  return result;
}
export function decodeGoalRouteContext(value: unknown): GoalRouteContext {
  const raw = requireJsonObject(value, "goal_route_context");
  const summary = requireJsonObject(raw.agent_todo_summary, "agent_todo_summary");
  let current: CurrentAction | null = null;
  if (raw.agent_lane_next_action !== null) {
    const item = requireJsonObject(raw.agent_lane_next_action, "agent_lane_next_action");
    current = {
      todo_id: nullableText(item.todo_id, "agent_lane_next_action.todo_id"),
      text: text(item.text, "agent_lane_next_action.text"),
      claim_required_before_work: requireBoolean(item.claim_required_before_work, "agent_lane_next_action.claim_required_before_work"),
    };
    for (const field of ["selected_by", "confidence", "source"] as const) {
      if (item[field] != null) current[field] = item[field];
    }
  }
  let frontier: Frontier | null = null;
  if (raw.agent_scope_frontier !== null) {
    const item = requireJsonObject(raw.agent_scope_frontier, "agent_scope_frontier");
    frontier = {
      action: text(item.action ?? "", "agent_scope_frontier.action"),
      effective_action: text(item.effective_action ?? "", "agent_scope_frontier.effective_action"),
      reason: text(item.reason ?? "", "agent_scope_frontier.reason"),
    };
  }
  return {
    agent_id: nullableText(raw.agent_id, "agent_id"),
    agent_todo_summary: {
      claimed_advancement_open_items: rows(summary.claimed_advancement_open_items, "claimed_advancement_open_items"),
      claimed_by_others_items: rows(summary.claimed_by_others_items, "claimed_by_others_items"),
      executable_backlog_items: rows(summary.executable_backlog_items, "executable_backlog_items"),
      unclaimed_priority_open_items: rows(summary.unclaimed_priority_open_items, "unclaimed_priority_open_items"),
      current_agent_handoff_gates: rows(summary.current_agent_handoff_gates, "current_agent_handoff_gates"),
      handoff_gates: rows(summary.handoff_gates, "handoff_gates"),
      current_agent_claimed_advancement_count: count(summary.current_agent_claimed_advancement_count, "current_agent_claimed_advancement_count"),
      claimed_advancement_open_count: count(summary.claimed_advancement_open_count, "claimed_advancement_open_count"),
    },
    agent_lane_next_action: current,
    agent_scope_frontier: frontier,
    agent_lane_frontier_hint: raw.agent_lane_frontier_hint === null ? null : requireJsonObject(raw.agent_lane_frontier_hint, "agent_lane_frontier_hint"),
    active_state_next_action: text(raw.active_state_next_action, "active_state_next_action"),
    selected_recommended_action: text(raw.selected_recommended_action, "selected_recommended_action"),
    latest_run_recommended_action: text(raw.latest_run_recommended_action, "latest_run_recommended_action"),
    actions_projection_aligned: requireBoolean(raw.actions_projection_aligned, "actions_projection_aligned"),
    goal_route_hint_override: override(raw.goal_route_hint_override),
  };
}
function actionable(row: RouteRow): boolean {
  return !row.done && (!row.status || row.status === "open") && row.task_class === "advancement_task";
}
function matchesClaim(row: RouteRow, agent: string, claim: "other" | "unclaimed"): boolean {
  return claim === "other" ? Boolean(row.claimed_by && row.claimed_by !== agent) : !row.claimed_by;
}
function compact(row: RouteRow, gate = false): JsonObject {
  const result: JsonObject = {};
  for (const field of ["todo_id", "claimed_by", "unblocks_todo_id"] as const) {
    if (row[field]) result[field] = row[field];
  }
  if (row.excluded_agents.length) result.excluded_agents = [...row.excluded_agents];
  if (gate) {
    if (row.gate_state != null) result.gate_state = row.gate_state;
  } else {
    if (row.blocks_agent) result.blocks_agent = row.blocks_agent;
    for (const field of ["priority", "action_kind"] as const) {
      if (row[field] != null) result[field] = row[field];
    }
    if (!row.todo_id) result.text = row.display_text;
  }
  return result;
}
function candidates(source: (RouteRow | null)[] | null, agent: string, claim: "other" | "unclaimed"): JsonObject[] {
  const result: JsonObject[] = [], seen = new Set<string>();
  for (const row of source ?? []) {
    if (!row || !actionable(row) || !matchesClaim(row, agent, claim) || !row.text) continue;
    const identity = row.todo_id || row.display_text;
    if (seen.has(identity)) continue;
    seen.add(identity);
    result.push(compact(row));
    if (result.length >= ITEM_LIMIT) break;
  }
  return result;
}
function countRows(source: (RouteRow | null)[] | null, agent: string, claim: "other" | "unclaimed"): number {
  return (source ?? []).filter((row) => row && actionable(row) && matchesClaim(row, agent, claim)).length;
}
export function projectGoalRouteHint(value: unknown): JsonObject | null {
  const facts = decodeGoalRouteContext(value), agent = facts.agent_id;
  if (!agent) return null;
  const summary = facts.agent_todo_summary, lane = facts.agent_lane_next_action;
  let current: JsonObject | null = null;
  if (lane && (lane.todo_id || lane.text)) {
    current = lane.todo_id ? { todo_id: lane.todo_id } : { text: lane.text };
    for (const field of ["selected_by", "confidence", "source"] as const) {
      if (lane[field] != null) current[field] = lane[field];
    }
    if (lane.claim_required_before_work) current.claim_required_before_work = true;
  }
  let other = candidates(summary.claimed_advancement_open_items, agent, "other");
  if (!other.length) other = candidates(summary.claimed_by_others_items, agent, "other");
  const source = summary.executable_backlog_items ?? summary.unclaimed_priority_open_items;
  const unclaimed = candidates(source, agent, "unclaimed");
  const gates: JsonObject[] = [], seenGates = new Set<string>();
  for (const row of summary.current_agent_handoff_gates ?? summary.handoff_gates ?? []) {
    if (!row || !row.excluded_agents.includes(agent) || row.gate_state !== "blocking" || seenGates.has(row.gate_identity)) continue;
    seenGates.add(row.gate_identity);
    gates.push(compact(row, true));
    if (gates.length >= ITEM_LIMIT) break;
  }
  const currentCount = summary.current_agent_claimed_advancement_count;
  const otherCount = Math.max(other.length, summary.claimed_advancement_open_count
    ? Math.max(0, summary.claimed_advancement_open_count - currentCount)
    : countRows(summary.claimed_advancement_open_items, agent, "other"));
  let decision = "durable_goal_route", reason = "no agent-lane override is needed; preserve durable goal Next Action";
  if (current) {
    if (current.claim_required_before_work === true) {
      decision = "claim_then_run_current_agent";
      reason = "quota selected an unclaimed in-scope todo for this agent lane";
    } else {
      decision = "run_current_agent_lane";
      reason = "quota selected a runnable current-agent lane todo";
    }
  } else if (facts.agent_scope_frontier !== null) {
    decision = facts.agent_scope_frontier.action || facts.agent_scope_frontier.effective_action || "agent_scope_frontier";
    reason = facts.agent_scope_frontier.reason || "agent-scope frontier blocks delivery";
  } else if (gates.length || other.length) {
    decision = "wait_or_reassign_other_agent_lane";
    reason = "visible advancement work is owned by another agent lane";
  } else if (unclaimed.length) {
    decision = "claim_unowned_in_scope";
    reason = "unclaimed advancement work is available to the current agent lane";
  }
  const hint: JsonObject = {
    schema_version: GOAL_ROUTE_HINT_SCHEMA_VERSION,
    kind: "agent_lane_synthesis", agent_id: agent, agent_model: "peer_v1",
    route_decision: decision, reason,
    preserves_goal_next_action: true, goal_next_action_mutation: "none",
    counts: {
      current_agent_claimed_advancement_count: currentCount,
      unclaimed_advancement_count: countRows(source, agent, "unclaimed"),
      other_agent_claimed_advancement_count: otherCount,
      blocking_handoff_gate_count: gates.length,
    },
    selected_action_differs_from_durable: Boolean(facts.active_state_next_action && facts.selected_recommended_action && !facts.actions_projection_aligned),
    has_durable_next_action: Boolean(facts.active_state_next_action),
    has_latest_run_recommended_action: Boolean(facts.latest_run_recommended_action),
  };
  if (current) hint.current_agent_next_action = current;
  if (other.length) hint.other_agent_next_actions = other;
  if (unclaimed.length) hint.unclaimed_next_actions = unclaimed;
  if (gates.length) hint.blocking_handoff_gates = gates;
  if (facts.agent_lane_frontier_hint !== null) {
    const frontier: JsonObject = {};
    for (const field of ["decision", "source", "reason_code", "target_todo_id", "quiet_noop_allowed"] as const) {
      if (facts.agent_lane_frontier_hint[field] != null) frontier[field] = facts.agent_lane_frontier_hint[field];
    }
    hint.frontier_hint = frontier;
  }
  const patch = facts.goal_route_hint_override;
  if (patch) {
    if (patch.omit_current_agent_next_action === true) delete hint.current_agent_next_action;
    for (const [field, value] of Object.entries(patch)) {
      if (field !== "omit_current_agent_next_action") hint[field] = value;
    }
  }
  return hint;
}
