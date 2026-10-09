import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {countTodoWork, decodeMonitorSchedule, monitorIsDue, monitorHasScheduleGap,
  type MonitorSchedule} from "./summary_lanes.ts";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireBoolean, requireInteger, requireStringArray,
  optionalNonEmptyString } from "../runtime_decode.ts";
import { projectTodoResumePlanning } from "./resume_planning.ts";
import { gateAddressesAgent, actionAddressesAgent, claimAllowsAgent } from "./agent_scope.ts";
import { missingRequiredCapabilities } from "../agents/capability_gate.ts";
import {claimedAdvancementCountFromIndex} from "./frontier_revision.ts";
import {authorityUnicodeCompare} from "../coordination/authority_store_codec.ts";
import type {HandoffState} from "./succession.ts";
import {validateTodoClosureSource} from "./succession.ts";

interface Row {
  payload: JsonObject; display: JsonObject; claim: string | null;
  bound: string | null; blocks: string | null; excluded: readonly string[];
  global: boolean; gate: boolean; removed: boolean; actionable: boolean;
  due: boolean; watchOnly: boolean; taskClass: string; priority: number; index: number;
  profileRank: number; missing: readonly string[]; rawClaimed: boolean;
  schedule: MonitorSchedule | null;
}

function decodeRow(value: unknown, available: readonly string[] | undefined, observedAt: number | null): Row {
  const raw = requireJsonObject(value, "quota selection row");
  const payload = requireJsonObject(raw.payload, "payload");
  const boolean = (key: string) => requireBoolean(raw[key], key);
  const integer = (key: string) => requireInteger(raw[key], key);
  const optional = (key: string) => optionalNonEmptyString(raw[key], key);
  const schedule = observedAt === null ? null : decodeMonitorSchedule(raw);
  return {payload, display: raw.display == null ? payload : requireJsonObject(raw.display, "display"),
    claim: optional("claim"), bound: optional("bound"), blocks: optional("blocks"),
    excluded: requireStringArray(raw.excluded, "excluded"), global: boolean("global"),
    gate: boolean("gate"), removed: boolean("removed"), actionable: boolean("actionable"),
    schedule, due: schedule && observedAt !== null ? monitorIsDue(schedule, observedAt) : boolean("due"),
    watchOnly: raw.watch_only === undefined ? false : boolean("watch_only"),
    taskClass: optional("task_class") ?? "advancement_task",
    priority: integer("priority"), index: integer("index"), profileRank: integer("profile_rank"),
    missing: available === undefined ? requireStringArray(raw.missing, "missing") :
      missingRequiredCapabilities(requireStringArray(raw.required, "required"), requireStringArray(raw.targets, "targets"), available),
    rawClaimed: boolean("raw_claimed")};
}

function rows(value: unknown, available: readonly string[] | undefined, observedAt: number | null): Row[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("quota rows must be an array");
  return value.map(row => decodeRow(row, available, observedAt));
}
const payloads = (items: readonly Row[]) => items.map(row => row.payload);
const compact = (items: readonly Row[], limit: number) => items.slice(0, limit).map(row => row.display);
const bucket = (row: Row, agent: string) => row.claim === agent ? 0 : row.claim === null ? 1 : 2;

/** A gate addresses a lane; it is not a job whose claim grants execution. */
function gateApplies(row: Row, agent: string | null): boolean {
  return !agent || gateAddressesAgent(row, agent);
}
function executableBy(row: Row, agent: string | null): boolean {
  return !agent || (!row.removed && claimAllowsAgent(row, agent));
}

/** Presentation-only claimant coverage; never changes eligible work or counts. */
function visibleClaims(items: readonly Row[], limit: number): Row[] {
  if (items.length <= limit) return items.slice(0, limit);
  const groups = new Map<string, Row[]>();
  for (const row of items) if (row.claim) {
    const group = groups.get(row.claim) ?? [];
    group.push(row); groups.set(row.claim, group);
  }
  if (!groups.size) return items.slice(0, limit);
  const cap = Math.max(1, Math.floor(limit / groups.size));
  const selected = new Set<Row>();
  for (const group of groups.values()) for (const row of group.slice(0, cap)) {
    if (selected.size < limit) selected.add(row);
  }
  for (const row of items) if (selected.size < limit) selected.add(row);
  return items.filter(row => selected.has(row)).slice(0, limit);
}

function visibility(items: readonly Row[], agent: string | null, backlog: number, limit: number): JsonObject {
  const claimed = items.filter(row => row.claim);
  const advancement = (values: readonly Row[]) => values.filter(row => row.actionable && row.taskClass === "advancement_task");
  const monitors = (values: readonly Row[]) => values.filter(row => row.actionable && row.taskClass === "continuous_monitor");
  const advanced = advancement(claimed), monitored = monitors(claimed);
  const result: JsonObject = {
    unclaimed_priority_open_items: compact(items.filter(row => !row.claim), backlog),
    claimed_open_items: compact(visibleClaims(claimed, limit), limit),
    claimed_advancement_open_items: compact(visibleClaims(advanced, limit), limit),
    claimed_monitor_open_items: compact(visibleClaims(monitored, limit), limit),
    ...(claimed.length ? {claimed_advancement_open_count: advanced.length, claimed_monitor_open_count: monitored.length} : {}),
  };
  if (agent) {
    const current = claimed.filter(row => row.claim === agent), others = claimed.filter(row => row.claim !== agent);
    const currentAdvanced = advancement(current), currentMonitors = monitors(current);
    Object.assign(result, {
      current_agent_claimed_open_items: compact(current, limit),
      current_agent_claimed_advancement_items: compact(currentAdvanced, limit),
      current_agent_claimed_monitor_items: compact(currentMonitors, limit),
      claimed_by_others_items: compact(others, backlog),
      current_agent_claimed_open_count: current.length,
      current_agent_claimed_advancement_count: currentAdvanced.length,
      current_agent_claimed_monitor_count: currentMonitors.length,
      claimed_by_others_count: others.length,
    });
  }
  return result;
}

function claimScope(source: readonly Row[], selected: readonly Row[], agent: string,
  profile: JsonObject | null, limit: number): JsonObject {
  const current = selected.filter(row => row.claim === agent), unclaimed = selected.filter(row => !row.claim);
  const others = source.filter(row => bucket(row, agent) === 2);
  const excluded = source.filter(row => row.excluded.includes(agent)), removed = source.filter(row => row.removed);
  const otherItems = compact(visibleClaims(others, limit), limit);
  return {
    schema_version: "agent_claim_scope_v0", agent_id: agent, agent_model: "peer_v1",
    selection_order: "current_agent_claimed_then_unclaimed", selectable_open_count: selected.length,
    current_agent_claimed_open_count: current.length, unclaimed_open_count: unclaimed.length,
    other_agent_claimed_open_count: others.length, other_agent_claimed_weight: "diagnostic_only",
    other_agent_claimed_items: otherItems, blocked_claimed_open_count: others.length, blocked_claimed_items: otherItems,
    executor_excluded_self_count: excluded.length, executor_excluded_self_items: compact(excluded, limit),
    executor_exclusion_policy: "excluded_agents_cannot_claim_or_execute",
    removed_continuation_blocked_count: removed.length, removed_continuation_blocked_items: compact(removed, limit),
    removed_continuation_policy: "legacy_review_handoffs_fail_closed_until_repaired",
    ...(profile ? {profile_routing: {schema_version: "agent_profile_routing_v0", applied: true,
      within_claim_bucket_only: true, preferred_action_kinds: profile.preferred_action_kinds ?? [],
      avoid_action_kinds: profile.avoid_action_kinds ?? []}} : {}),
  };
}

export function projectQuotaSelection(value: unknown, observedAt: number | null = null,
  includeGateItems = false): JsonObject {
  const request = requireJsonObject(value, "quota selection");
  const available = request.available === undefined ? undefined : requireStringArray(request.available, "available");
  const source = rows(request.items, available, observedAt), active = rows(request.active_items, available, observedAt), activeExecutable = rows(request.active_executable_items, available, observedAt);
  const agent = optionalNonEmptyString(request.agent_id, "agent_id");
  const userMode = requireBoolean(request.user_gate_scope, "user_gate_scope");
  const supported = requireBoolean(request.monitor_supported, "monitor_supported");
  const limit = (key: string) => {
    const result = requireInteger(request[key], key);
    if (result < 0) throw new EffectRuntimeRequestError(`${key} must be nonnegative`);
    return result;
  };
  const diagnostic = limit("diagnostic_limit"), backlog = limit("backlog_limit"), visibilityLimit = limit("visibility_limit");
  const profile = request.profile == null ? null : requireJsonObject(request.profile, "profile");
  const gates = userMode ? source.filter(row => row.gate) : source;
  const blocking = userMode ? gates.filter(row => gateApplies(row, agent)) : gates;
  const otherGates = userMode ? gates.filter(row => !gateApplies(row, agent)) : [];
  const actions = userMode ? source.filter(row => !row.gate && actionAddressesAgent(row, agent)) : [];
  const otherActions = userMode ? source.filter(row => !row.gate && !actionAddressesAgent(row, agent)) : [];
  // Explicit User gate scope has already decided blocking. Claim/exclusion
  // governs Agent execution, not permission to disregard that human gate.
  const open = userMode ? blocking : blocking.filter(row => executableBy(row, agent));
  const executionRank = (a: Row, b: Row) =>
    (agent && !userMode ? bucket(a, agent) - bucket(b, agent) || a.profileRank - b.profileRank : 0) ||
    a.priority - b.priority;
  if (agent && !userMode) open.sort((a, b) => executionRank(a, b) || a.index - b.index);
  const scope = agent && !userMode ? claimScope(blocking, open, agent, profile, diagnostic) : null;
  const monitors = open.filter(row => row.actionable && row.taskClass === "continuous_monitor");
  const due = supported ? monitors.filter(row => row.due && executableBy(row, agent)) : [];
  const admittedDue = due.filter(row => !row.missing.length);
  // Timed eligibility guarantees a non-null due instant. Select from the full
  // admitted set before transport caps: a recurring low-index Monitor must not
  // starve older equally ranked work. Untimed legacy requests keep their order.
  if (observedAt !== null) admittedDue.sort((a, b) => executionRank(a, b) ||
    a.schedule!.dueAt! - b.schedule!.dueAt! || a.index - b.index);
  const watchOnlyMonitors = monitors.filter(row => row.watchOnly);
  // Gap presentation keeps source priority/index order, independent of the
  // claim/profile buckets used to select executable work.
  const monitorSet = new Set(monitors);
  const scheduleGaps = observedAt === null || !supported ? [] : source
    .filter(row => monitorSet.has(row) && row.schedule !== null && monitorHasScheduleGap(row.schedule, observedAt))
    .sort((a, b) => a.priority - b.priority || a.index - b.index);
  const activeVisible = (row: Row) => userMode ? (row.gate ? gateApplies(row, agent) : actionAddressesAgent(row, agent)) : executableBy(row, agent);
  const gateFilter = otherGates.length ? {
    schema_version: "agent_scoped_user_gate_filter_v0", agent_id: agent,
    policy: "user todos scoped to another agent by blocks_agent or claimed_by remain visible but do not block this agent's quota lane",
    current_agent_blocking_open_count: blocking.length, other_agent_scoped_open_count: otherGates.length,
  } : null;
  const actionFilter = otherActions.length ? {
    schema_version: "agent_scoped_user_action_filter_v0", agent_id: agent,
    policy: "user actions bound to another agent remain diagnostic-only and must not enter this agent's reminder channel",
    current_agent_user_action_open_count: actions.length, other_agent_bound_user_action_open_count: otherActions.length,
  } : null;
  const displayed = userMode ? [...open, ...actions] : open;
  const sourceComplete = request.source_open_count === source.length &&
    (request.source_complete === undefined || requireBoolean(request.source_complete, "source_complete"));
  const countOpen = !agent && !userMode && Number.isSafeInteger(request.source_open_count)
    ? Math.max(Number(request.source_open_count), displayed.length) : displayed.length;
  const claimVisibility = visibility(blocking, agent, backlog, visibilityLimit);
  if (agent && !userMode) {
    const completeCount = claimedAdvancementCountFromIndex(request.frontier_revision_index, agent);
    if (completeCount !== null) claimVisibility.current_agent_claimed_advancement_count = completeCount;
  }
  return {work_counts: countTodoWork(displayed, countOpen, sourceComplete, agent && !userMode ? agent : null), lanes: {
    all_open_items: payloads(source), blocking_open_items: payloads(blocking),
    user_action_open_items: payloads(actions), other_agent_bound_user_action_items: payloads(otherActions),
    user_action_agent_scope_filter: actionFilter, other_agent_scoped_items: payloads(otherGates),
    agent_scope_filter: gateFilter, open_items: payloads(open), claim_scope: scope,
    executable_items: payloads(open.filter(row => row.actionable && row.taskClass === "advancement_task")),
    ...(includeGateItems ? {gate_items: payloads(open.filter(row => row.gate))} : {}),
    monitor_items: payloads(monitors), monitor_due_items: payloads(admittedDue),
    ...(observedAt === null ? {} : {monitor_schedule_gap_items: payloads(scheduleGaps)}),
    watch_only_monitor_items: payloads(watchOnlyMonitors),
    watch_only_monitor_due_items: payloads(admittedDue.filter(row => row.watchOnly)),
    non_watch_only_monitor_due_items: payloads(admittedDue.filter(row => !row.watchOnly)),
    monitor_capability_blocked_due_items: due.filter(row => row.missing.length).map(row => ({...row.display, missing_capabilities: [...row.missing]})),
    claimed_open_items: payloads(blocking.filter(row => row.rawClaimed)),
    display_open_items: payloads(userMode ? [...open, ...actions] : open),
    active_next_action_items: active.filter(activeVisible).map(row => row.display),
    active_next_action_executable_items: activeExecutable.filter(activeVisible).map(row => row.display),
    open_count: userMode ? open.length + actions.length : scope ? open.length : request.source_open_count,
  }, claim_visibility: claimVisibility};
}

/** Route hints are read visibility, never a grant to execute or clear a gate.
 * Reuse the ordinary claim/exclusion owner within the same planning crossing. */
function routeContinuationLanes(value: unknown, agent: string | null, limit: number): JsonObject {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("route items must be an array");
  const seen = new Set<string>();
  const selected = value.flatMap(value => {
    const row = requireJsonObject(value, "route item");
    const display = requireJsonObject(row.display, "route display");
    if (typeof row.identity !== "string" || (row.replan !== null && typeof row.replan !== "boolean") ||
        (row.task_class !== null && typeof row.task_class !== "string")) {
      throw new EffectRuntimeRequestError("invalid route identity or classification facts");
    }
    const gate = requireBoolean(row.gate, "route gate");
    const scope = {claim: optionalNonEmptyString(row.claim, "route claim"),
      excluded: requireStringArray(row.excluded, "route excluded")};
    const sort = row.sort;
    if (!Array.isArray(sort) || sort.length !== 4 ||
        !Number.isSafeInteger(sort[0]) || !Number.isSafeInteger(sort[1]) ||
        typeof sort[2] !== "string" || typeof sort[3] !== "string") {
      throw new EffectRuntimeRequestError("invalid route presentation coordinates");
    }
    if (row.replan === false || (gate && row.replan !== true) ||
        (row.task_class !== null && row.task_class !== "advancement_task") ||
        !row.identity || seen.has(row.identity)) return [];
    seen.add(row.identity);
    return [{scope, sort: sort as [number, number, string, string],
      display: {...display, route_continuation_replan_required: true}}];
  });
  selected.sort((a, b) => a.sort[0] - b.sort[0] || a.sort[1] - b.sort[1] ||
    authorityUnicodeCompare(a.sort[2], b.sort[2]) || authorityUnicodeCompare(a.sort[3], b.sort[3]));
  if (!selected.length) return {};
  const compact = (rows: typeof selected) => rows.slice(0, limit).map(row => row.display);
  const result: JsonObject = {route_continuation_replan_count: selected.length,
    route_continuation_replan_candidates: compact(selected)};
  if (agent) {
    const current = selected.filter(row => claimAllowsAgent(row.scope, agent));
    // Historical unclaimed visibility includes excluded rows; only the current
    // lane permits a wake, and its consumer rechecks execution eligibility.
    const unclaimed = selected.filter(row => !row.scope.claim);
    const other = selected.filter(row => !claimAllowsAgent(row.scope, agent));
    Object.assign(result, {
      current_agent_route_continuation_replan_count: current.length,
      current_agent_route_continuation_replan_candidates: compact(current),
      unclaimed_route_continuation_replan_count: unclaimed.length,
      unclaimed_route_continuation_replan_candidates: compact(unclaimed),
      other_agent_route_continuation_replan_count: other.length,
      other_agent_route_continuation_replan_candidates: compact(other),
      route_continuation_replan_selection_policy: "quota may wake the current peer for route continuation replan " +
        "candidates claimed by that agent or unclaimed; other-agent route candidates remain diagnostic visibility",
    });
  }
  return result;
}

/** Exclusion addresses a handoff review lane; it is not execution eligibility.
 * Preserve projected source order, duplicates and unknown historical display states. */
function handoffGateLanes(value: unknown, agent: string | null, limit: number): JsonObject {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("handoff items must be an array");
  const rows = value.map(value => {
    const row = requireJsonObject(value, "handoff item");
    return {display: requireJsonObject(row.display, "handoff display"),
      excluded: requireStringArray(row.excluded, "handoff excluded")};
  });
  if (!rows.length) return {};
  const compact = (items: typeof rows) => items.slice(0, limit).map(row => row.display);
  const result: JsonObject = {handoff_gate_count: rows.length, handoff_gates: compact(rows)};
  if (agent) {
    const current = rows.filter(row => row.excluded.includes(agent));
    const needsSuccessor: HandoffState = "cleared_without_successor";
    const cleared = current.filter(row => row.display.gate_state === needsSuccessor);
    Object.assign(result, {current_agent_handoff_gate_count: current.length,
      current_agent_handoff_gates: compact(current),
      current_agent_cleared_without_successor_handoff_count: cleared.length,
      current_agent_cleared_without_successor_handoff_gates: compact(cleared)});
  }
  return result;
}

const frontierLanes = ["current_agent_claimed_monitor_items", "monitor_open_items", "gate_open_items",
  "deferred_resume_candidates", "current_agent_deferred_resume_candidates", "resume_blocked_items",
  "current_agent_monitor_blocked_resume_candidates", "current_agent_handoff_gates"];
const object = (value: unknown): JsonObject | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
const timestamp = (value: unknown) => typeof value === "string" ? parseTodoTimestampMicros(value.trim()) : null;
const text = (value: unknown) => String(value ?? "").trim();
function identity(row: JsonObject): string {
  for (const key of ["todo_id", "target_key", "action_kind", "title", "text"]) {
    const value = text(row[key]);
    if (value) return value;
  }
  return row.index == null ? "frontier_transition" : String(row.index);
}
function source(row: JsonObject, lane: string): string {
  const taskClass = text(row.task_class);
  if (taskClass) return taskClass;
  // Preserve historical raw-summary classification; typed current records
  // carry task_class. This is a display codec, never admission authority.
  const action = text(row.action_kind).toLowerCase();
  if (action.includes("monitor")) return "continuous_monitor";
  if (action.includes("gate") || action.includes("approval")) return "user_gate";
  if (lane.includes("monitor")) return "continuous_monitor";
  if (lane.includes("gate")) return "user_gate";
  return "frontier_todo";
}
function utcIso(micros: bigint): string {
  // Match datetime.isoformat, including sub-millisecond deadlines and years
  // before 1970. The existing codec validates calendars and timezone offsets.
  const seconds = micros >= 0n ? micros / 1000000n : (micros - 999999n) / 1000000n;
  const fraction = micros - seconds * 1000000n;
  const date = new Date(Number(seconds * 1000n)).toISOString().slice(0, -5);
  return `${date}${fraction ? `.${String(fraction).padStart(6, "0")}` : ""}+00:00`;
}
function projectTodoFrontierDeadline(summary: JsonObject, now: bigint): JsonObject | null {
  const projected = object(summary.frontier_deadline);
  const projectedTime = timestamp(projected?.next_due_at);
  if (projected && projectedTime !== null && projectedTime > now) return {...projected};
  const seen = new Set<string>();
  let selected: JsonObject | null = null, earliest: bigint | null = null;
  for (const lane of frontierLanes) {
    const items = summary[lane];
    if (!Array.isArray(items)) continue;
    for (const raw of items) {
      const row = object(raw);
      if (!row) continue;
      const expiry = timestamp(row.expires_at), due = timestamp(row.next_due_at);
      if (expiry !== null && expiry <= now || due === null || due <= now) continue;
      const id = identity(row), key = JSON.stringify([id, String(due)]);
      if (seen.has(key)) continue;
      seen.add(key);
      if (earliest === null || due < earliest) {
        earliest = due;
        selected = {identity: id, source: source(row, lane), next_due_at: utcIso(due)};
      }
    }
  }
  return selected ? {schema_version: "todo_frontier_deadline_v0", ...selected, candidate_count: seen.size} : null;
}

/** One quota read boundary composes scope/claim selection, resume, hint
 * visibility and the typed source closure rules. */
export function projectTodoQuotaPlanning(value: unknown): JsonObject {
  const request = requireJsonObject(value, "quota planning");
  if (!["todo_quota_planning_request_v0", "todo_quota_planning_request_v1", "todo_quota_planning_request_v2",
      "todo_quota_planning_request_v3"].includes(String(request.schema_version))) {
    throw new EffectRuntimeRequestError("quota planning schema mismatch");
  }
  const selection = requireJsonObject(request.selection, "selection");
  if (request.schema_version !== "todo_quota_planning_request_v0") {
    requireStringArray(selection.available, "available");
  }
  // v2 already owns route/handoff visibility and closure. Only v3 requires
  // the shared monitor clock; older callers retain their exact input contract.
  const observedAt = request.schema_version === "todo_quota_planning_request_v3" ? selection.observed_at : null;
  if (observedAt !== null && (typeof observedAt !== "number" || !Number.isFinite(observedAt))) {
    throw new EffectRuntimeRequestError("quota monitor observed_at must be finite");
  }
  if (request.schema_version === "todo_quota_planning_request_v3" && observedAt === null) {
    throw new EffectRuntimeRequestError("quota monitor observed_at is required");
  }
  const hasSourceContract = request.schema_version === "todo_quota_planning_request_v2" ||
    request.schema_version === "todo_quota_planning_request_v3";
  const closure = hasSourceContract ? validateTodoClosureSource(request.source_contract) : {};
  const now = request.current_time === undefined ? null :
    typeof request.current_time === "string" ? parseTodoTimestampMicros(request.current_time) : null;
  if (request.current_time !== undefined && now === null) throw new EffectRuntimeRequestError("invalid quota planning clock");
  if (now !== null && observedAt !== null && Number(now) / 1000000 !== observedAt) {
    throw new EffectRuntimeRequestError("quota planning clocks must match");
  }
  const resume = projectTodoResumePlanning(request.resume);
  const selected = projectQuotaSelection(selection, observedAt, now !== null);
  const agent = optionalNonEmptyString(selection.agent_id, "agent_id");
  const limit = requireInteger(selection.backlog_limit, "backlog_limit");
  const handoff = hasSourceContract ? handoffGateLanes(request.handoff_items, agent, limit) : {};
  const route = hasSourceContract ? routeContinuationLanes(request.route_items, agent, limit) : {};
  const lanes = requireJsonObject(selected.lanes, "selection lanes");
  const frontier = now === null ? null : projectTodoFrontierDeadline({
    ...requireJsonObject(selected.claim_visibility, "claim visibility"),
    ...requireJsonObject(resume.deferred_lanes, "deferred lanes"),
    ...requireJsonObject(resume.resume_blocked_lanes, "resume blocked lanes"), ...handoff,
    frontier_deadline: request.frontier_deadline ?? null,
    monitor_open_items: lanes.monitor_items,
    gate_open_items: lanes.gate_items,
  }, now);
  // The full gate lane stays inside this batch. Transport only the original
  // three-row display after deadline selection, rather than duplicating bodies.
  if (now !== null) lanes.gate_items = (lanes.gate_items as JsonObject[]).slice(0, 3);
  return {schema_version: "todo_quota_planning_v0", resume_planning: resume, ...selected, ...closure,
    ...(hasSourceContract ? {handoff_lanes: handoff, route_lanes: route} : {}),
    ...(now === null ? {} : {frontier_deadline: frontier})};
}
