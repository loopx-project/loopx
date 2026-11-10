import {COORDINATION_STATE_CONTRACT} from "../coordination/coordination_state_contract.generated.ts";
import {decodeTodoReadSource, readAgent, readExcludedAgents, readCapabilities, readResume,
  pythonFieldText, pythonFieldIsInteger, pythonFieldIsList, pythonFieldInteger, writeScopes, readScope, readScopes, taskClass} from "./read_codec.ts";
import {readTodoPriority, todoPriorityRank} from "./priority.ts";
import {removedTodoContinuationPolicy} from "./legacy_continuation_policy_migration.ts";
import {stripPythonWhitespace} from "../coordination/todo_agents.ts";
import {projectTodoSummaryDisplay, projectTodoContextPage} from "./context_projection.ts";
import {isBoundedBlockedRetry} from "../quota/settlement_phase.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {countTodoWork, decodeMonitorSchedule, monitorIsDue, monitorHasScheduleGap,
  type MonitorSchedule} from "./summary_lanes.ts";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireBoolean, requireInteger, requireStringArray,
  optionalNonEmptyString } from "../runtime_decode.ts";
import { projectTodoResumePlanning, materializeResumePlanningRequest } from "./resume_planning.ts";
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
function scopedReceiptRetry(row: JsonObject, due: bigint, agent: string | null): JsonObject | null {
  const retry = object(row.blocked_retry);
  return row.status === "open" && row.task_class === "advancement_task" &&
    row.resume_ready === false && retry?.source === "turn_settlement" &&
    typeof retry.turn_instance_id === "string" && !!retry.turn_instance_id &&
    retry.agent_id === agent && agent !== null && typeof retry.goal_id === "string" &&
    (!row.claimed_by || row.claimed_by === retry.agent_id) &&
    row.resume_when === retry.resume_when && timestamp(retry.due_at) === due &&
    isBoundedBlockedRetry(retry, typeof row.todo_id === "string" ? row.todo_id : null)
      ? retry : null;
}
function projectTodoFrontier(summary: JsonObject, now: bigint, agent: string | null): JsonObject {
  const projected = object(summary.frontier_deadline);
  const projectedTime = timestamp(projected?.next_due_at);
  const currentProjection = projected && projectedTime !== null && projectedTime > now ? projected : null;
  const seen = new Set<string>();
  let selected: JsonObject | null = currentProjection;
  let earliest: bigint | null = currentProjection ? projectedTime : null;
  let addedRetryCount = 0;
  let retryWait: JsonObject | null = null;
  let retryDue: bigint | null = null;
  for (const lane of currentProjection ? ["resume_blocked_items"] : frontierLanes) {
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
      const retry = scopedReceiptRetry(row, due, agent);
      const receiptRetry = retry !== null;
      // A receipt wait owns one unfinished Todo; the earliest wakeup may
      // belong to a different monitor. Keep both facts from this full source.
      if (receiptRetry && (retryDue === null || due < retryDue)) {
        retryDue = due;
        retryWait = retry;
      }
      if (currentProjection) {
        // The full-source cache retains deadlines omitted from visible lanes.
        // Only newly overlaid receipt retries can predate that current cache.
        if (!receiptRetry || due >= projectedTime!) continue;
        if (id !== currentProjection.identity) addedRetryCount += 1;
      }
      if (earliest === null || due < earliest) {
        earliest = due;
        selected = {identity: id, source: receiptRetry ? "turn_settlement" : source(row, lane),
          next_due_at: utcIso(due), ...(receiptRetry ? {blocked_retry: retry} : {})};
      }
    }
  }
  const candidateCount = currentProjection
    ? Math.max(1, Number(currentProjection.candidate_count) || 0) + addedRetryCount : seen.size;
  const deadline = currentProjection && selected === currentProjection ? {...currentProjection} :
    selected ? {schema_version: "todo_frontier_deadline_v0", ...selected, candidate_count: candidateCount} : null;
  return {frontier_deadline: deadline, ...(retryWait ? {blocked_retry_wait: retryWait} : {})};
}

function quotaPlanningWire(planning: JsonObject): JsonObject {
  const records: JsonObject[] = [];
  const references = new Map<JsonObject, number>();
  const recordLanes: string[] = [];
  const lanes = Object.fromEntries(Object.entries(requireJsonObject(planning.lanes, "planning lanes"))
    .map(([name, entries]) => {
      if (!Array.isArray(entries)) return [name, entries];
      recordLanes.push(name);
      return [name, entries.map(entry => {
        const record = requireJsonObject(entry, "planning record");
        // Share only the same producer object; equal IDs can carry different facts.
        let reference = references.get(record);
        if (reference === undefined) {
          reference = records.length;
          records.push(record);
          references.set(record, reference);
        }
        return reference;
      })];
    }));
  return {schema_version: "todo_quota_planning_wire_v1",
    planning: {...planning, lanes}, records, record_lanes: recordLanes};
}

/** One quota read boundary composes scope/claim selection, resume, hint
 * visibility and the typed source closure rules. */
export function projectTodoQuotaPlanning(value: unknown): JsonObject {
  let request = requireJsonObject(value, "quota planning");
  if (request.schema_version === "todo_profile_rank_request_v0") {
    return {rank: quotaProfileRank(requireJsonObject(request.facts, "profile rank facts"))};
  }
  if (request.summary_request !== undefined) request = prepareQuotaSummary(request);
  if (!["todo_quota_planning_request_v0", "todo_quota_planning_request_v1", "todo_quota_planning_request_v2",
      "todo_quota_planning_request_v3", "todo_quota_planning_request_v4"].includes(String(request.schema_version))) {
    throw new EffectRuntimeRequestError("quota planning schema mismatch");
  }
  const selection = {...requireJsonObject(request.selection, "selection")};
  for (const key of ["items", "active_items", "active_executable_items"]) {
    if (!Array.isArray(selection[key])) continue;
    selection[key] = (selection[key] as unknown[]).map(value => {
      const row = requireJsonObject(value, "selection display source");
      return row.display_source === undefined ? row : {...row,
        display: projectTodoSummaryDisplay({items: [row.display_source], strip_text: true})[0]};
    });
  }
  if (request.schema_version !== "todo_quota_planning_request_v0") {
    requireStringArray(selection.available, "available");
  }
  // v4 changes transport only; v3's shared clock and older contracts stay intact.
  const referenced = request.schema_version === "todo_quota_planning_request_v4";
  const clocked = referenced || request.schema_version === "todo_quota_planning_request_v3";
  const observedAt = clocked ? selection.observed_at : null;
  if (observedAt !== null && (typeof observedAt !== "number" || !Number.isFinite(observedAt))) {
    throw new EffectRuntimeRequestError("quota monitor observed_at must be finite");
  }
  if (clocked && observedAt === null) {
    throw new EffectRuntimeRequestError("quota monitor observed_at is required");
  }
  const hasSourceContract = request.schema_version === "todo_quota_planning_request_v2" ||
    clocked;
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
  const resumeBlocked = requireJsonObject(resume.resume_blocked_lanes, "resume blocked lanes");
  // An overlay can add a receipt wait after the stored resume lane was built.
  // Read its scoped full-source row; an existing nonempty display lane must
  // neither hide that retry nor impose its visibility limit on retry clocks.
  const receiptWaits = now === null ? [] : rows(selection.items,
    selection.available === undefined ? undefined : requireStringArray(selection.available, "available"),
    observedAt).filter(row => {
      const due = timestamp(row.payload.next_due_at);
      return executableBy(row, agent) && due !== null && due > now &&
        scopedReceiptRetry(row.payload, due, agent) !== null;
    }).map(row => row.payload);
  const frontier = now === null ? null : projectTodoFrontier({
    ...requireJsonObject(selected.claim_visibility, "claim visibility"),
    ...requireJsonObject(resume.deferred_lanes, "deferred lanes"),
    ...resumeBlocked, ...handoff,
    // Retain the display lane and union the existing full-source successor
    // waits before choosing a deadline; visibility limits are not clock limits.
    resume_blocked_items: [
      ...(Array.isArray(resumeBlocked.resume_blocked_items) ? resumeBlocked.resume_blocked_items : []),
      ...(resume.blocked_successor_items as JsonObject[]),
      ...receiptWaits,
    ],
    frontier_deadline: request.frontier_deadline ?? null,
    monitor_open_items: lanes.monitor_items,
    gate_open_items: lanes.gate_items,
  }, now, agent);
  // The full gate lane stays inside this batch. Transport only the original
  // three-row display after deadline selection, rather than duplicating bodies.
  if (now !== null) lanes.gate_items = (lanes.gate_items as JsonObject[]).slice(0, 3);
  const planning = {schema_version: "todo_quota_planning_v0", resume_planning: resume, ...selected, ...closure,
    ...(hasSourceContract ? {handoff_lanes: handoff, route_lanes: route} : {}),
    ...(now === null ? {} : frontier)};
  if (request.summary_request !== undefined) {
    const wire = composeQuotaSummary(request, planning);
    const summary = requireJsonObject(request.summary_request, "summary request");
    if (summary.consume_display_views === true) {
      const display = {source: summary.source, handoff_gates: (request.handoff_items as JsonObject[]).map(row => row.display)};
      wire.display_views = projectTodoContextPage({summary_display_views: display});
    }
    if (summary.consume_vision_wait_basis === true) {
      if (summary.consume_display_views !== true) throw new EffectRuntimeRequestError("wait basis requires explicitly consumed terminal source rows");
      const terminal = requireJsonObject(wire.display_views, "display views").terminal as JsonObject[];
      const source = {items: terminal, deferred_items: terminal.filter(row => row.status === "deferred"),
        resume_blocked_items: terminal.filter(row => row.resume_ready === false && row.status === "open"),
        current_agent_blocker_items: terminal};
      wire.vision_wait_basis = {
        resume_planning: projectTodoResumePlanning(materializeResumePlanningRequest(source, {agent_id: selection.agent_id, item_limit: 5})),
        current_agent_blocker_items: terminal.map(row => Object.fromEntries(["todo_id", "task_class", "status", "priority", "claimed_by", "excluded_agents", "reason"].filter(key => Object.hasOwn(row, key)).map(key => [key, row[key]]))),
        lineage_source_items: terminal.map(row => Object.fromEntries(["todo_id", "successor_todo_ids", "resume_condition"].filter(key => Object.hasOwn(row, key)).map(key => [key, row[key]]))),
      };
    }
    if (summary.consume_source_summary === true) wire.source_summary = projectTodoSummaryDisplay({source: summary.source,
      handoff_gates: (request.handoff_items as JsonObject[]).map(row => row.display)});
    return wire;
  }
  return referenced ? quotaPlanningWire(planning) : planning;
}

type Path = (string | number)[];
const summaryObject = (v: unknown): v is JsonObject => !!v && typeof v === "object" && !Array.isArray(v);
const truth = (v: unknown): boolean => Array.isArray(v) ? !!v.length : summaryObject(v) ? !!Object.keys(v).length : !!v;
const get = (v: JsonObject, k: string, fallback: unknown = null): unknown => Object.hasOwn(v, k) ? v[k] : fallback;
const summaryRows = (v: unknown): JsonObject[] => Array.isArray(v) ? v.filter(summaryObject) : [];

/** One advisory ranking owner, used by native materialization and the legacy codec. */
function quotaProfileRank(facts: JsonObject): number {
  const avoid = requireBoolean(facts.avoid, "avoid"), prefer = requireBoolean(facts.prefer, "prefer");
  const defaultClass = requireBoolean(facts.default_class, "default_class");
  return avoid ? 2 : prefer || defaultClass ? 0 : 1;
}

function nativeProfileRank(item: JsonObject, profile: JsonObject | null): number {
  if (profile === null) return 1;
  const action = stripPythonWhitespace(pythonFieldText(item, "action_kind")).toLowerCase();
  const valid = /^[a-z][a-z0-9_-]{0,63}$/.test(action);
  // Native profiles have already passed the registered profile grammar: only
  // literal action characters and '*' are allowed. Legacy fnmatch stays a codec.
  const matches = (value: unknown) => valid && Array.isArray(value) && value.some(pattern => {
    if (typeof pattern !== "string" || !/^[a-z0-9][a-z0-9_*-]{0,79}$/.test(pattern)) {
      throw new EffectRuntimeRequestError("native quota profile requires normalized action patterns");
    }
    return new RegExp("^" + pattern.split("*").join(".*") + "$", "u").test(action);
  });
  const explicit = stripPythonWhitespace(pythonFieldText(item, "task_class")).toLowerCase();
  return quotaProfileRank({avoid: matches(profile.avoid_action_kinds), prefer: matches(profile.preferred_action_kinds),
    default_class: !!explicit && Array.isArray(profile.default_task_classes) && profile.default_task_classes.includes(explicit)});
}

/** Materialize facts from the complete source, preserving lane/ordinal lineage.
 * This is the same quota owner, not another Todo eligibility or authority rule. */
export function materializeQuotaRow(item: JsonObject, profile: JsonObject | null): JsonObject {
  const text = (key: string) => stripPythonWhitespace(pythonFieldText(item, key));
  const classification = taskClass({...item, text: ["title", "text"].filter(key => text(key)).map(key => pythonFieldText(item, key)).join(" ")});
  const rawIndex = item.index;
  const index = pythonFieldInteger(item, "index");
  const priority = todoPriorityRank(readTodoPriority(Object.hasOwn(item, "priority") ? item :
    {...item, text: truth(item.text) ? pythonFieldText(item, "text") : pythonFieldText(item, "title")}));
  const schema = item.schema_version, legacySchema = COORDINATION_STATE_CONTRACT.todo_read_record.item_schema_version;
  const nativeSchema = COORDINATION_STATE_CONTRACT.todo_domain_record.item_schema_version;
  const legacy = schema === legacySchema || schema == null && typeof item.source_section === "string" && !!item.source_section.trim();
  const unknown = schema != null && schema !== legacySchema && schema !== nativeSchema;
  const address = (legacy || unknown) && pythonFieldIsInteger(item, "index") && Number(rawIndex) >= 0 ? Number(rawIndex) : null;
  const status = text("status").toLowerCase();
  // The retained action-kind substring heuristic only classifies legacy gates.
  // Explicit task_class always wins; this never broadens a permission boundary.
  const action = text("action_kind").toLowerCase();
  const gate = text("task_class") ? classification === "user_gate" : classification === "user_gate" ||
    ["approval", "approve", "boundary", "gate", "blocker", "credential", "private", "production", "leaderboard", "submission", "public_claim"].some(hint => action.includes(hint));
  const instant = (key: string) => {
    const value = parseTodoTimestampMicros(text(key));
    if (value === null) return null;
    // Convert decimal seconds once; rounding the microsecond integer first
    // loses valid distant-clock facts before the quota owner compares them.
    const absolute = value < 0n ? -value : value;
    return Number(`${value < 0n ? "-" : ""}${absolute / 1000000n}.${String(absolute % 1000000n).padStart(6, "0")}`);
  };
  return {presentation: [priority, address ?? 999999, address === null ? pythonFieldText(item, truth(item.completed_at) ? "completed_at" : "updated_at") : "",
      address === null ? pythonFieldText(item, "todo_id") : ""],
    claim: readAgent(item.claimed_by), bound: readAgent(item.bound_agent), blocks: readAgent(item.blocks_agent),
    excluded: readExcludedAgents(item.excluded_agents),
    global: item.global_gate === true || ["1", "true", "yes", "y", "global_gate", "global-gate"].includes(text("global_gate").toLowerCase()),
    gate, removed: removedTodoContinuationPolicy(text("removed_continuation_policy")) !== null ||
      removedTodoContinuationPolicy(text("continuation_policy")) !== null,
    actionable: item.done !== true && (!status || status === "open") &&
      (!readResume(item.resume_when) || item.resume_ready === true) &&
      !(summaryObject(item.goal_acceptance_guard) && item.goal_acceptance_guard.allowed === false),
    due_at: instant("next_due_at"), expires_at: instant("expires_at"),
    watch_only: classification === "continuous_monitor" && (item.watch_only === true || text("watch_only").toLowerCase() === "true"),
    task_class: classification, priority, index, profile_rank: nativeProfileRank(item, profile),
    required: readCapabilities(item.required_capabilities), targets: readCapabilities(item.target_capabilities), raw_claimed: truth(item.claimed_by)};
}

/** Source selection is part of the existing quota read, using the display owner. */
export function prepareQuotaSummary(request: JsonObject): JsonObject {
  const summary = requireJsonObject(request.summary_request, "summary request");
  const source = decodeTodoReadSource(requireJsonObject(summary.source, "summary source"));
  const legacy = summary.kind === "project_asset" && !(
    (Array.isArray(source.items) || Array.isArray(source.first_open_items)) &&
    ["total_count", "open_count", "done_count"].some(k => Object.hasOwn(source, k)));
  if (summary.kind !== "user" && summary.kind !== "project_asset") {
    throw new EffectRuntimeRequestError("invalid quota summary kind");
  }
  const materialize = summary.materialize_source === true;
  const pool = materialize ? null : requireJsonObject(summary.source_rows, "source rows");
  const profile = summaryObject(requireJsonObject(request.selection, "selection").profile)
    ? requireJsonObject(request.selection, "selection").profile as JsonObject : null;
  const selectedFacts: JsonObject[] = [];
  let selected: JsonObject[] = projectTodoSummaryDisplay({source: summary.source,
    handoff_gates: (request.handoff_items as JsonObject[]).map(row => row.display)}, (key, index) => {
      const lane = materialize ? source[key] : pool![key];
      if (!Array.isArray(lane) || materialize && !pythonFieldIsList(source, key)) throw new EffectRuntimeRequestError("quota summary source lane missing");
      const row = requireJsonObject(lane[index], "quota source fact");
      selectedFacts.push(materialize ? materializeQuotaRow(row, profile) : row);
    }).map((payload, index) => {
      const fact = selectedFacts[index]!;
      const presentation = [...fact.presentation as [number, number, string, string]];
      // Display rows already crossed JSON in the previous transaction. Integral
      // floats therefore supplied Python int coordinates at that boundary.
      const legacySchema = COORDINATION_STATE_CONTRACT.todo_read_record.item_schema_version;
      const legacy = payload.schema_version === legacySchema || payload.schema_version == null && truth(payload.source_section);
      const unknownSchema = payload.schema_version != null && ![legacySchema, COORDINATION_STATE_CONTRACT.todo_domain_record.item_schema_version].some(schema => schema === payload.schema_version);
      if ((legacy || unknownSchema) && typeof payload.index === "number" && Number.isInteger(payload.index) && payload.index >= 0) {
        presentation[1] = payload.index; presentation[2] = ""; presentation[3] = "";
      }
      return {...fact, presentation, payload, display_source: {record: payload}};
    });
  if (legacy && !selected.length && summary.next_fact) {
    const fact = requireJsonObject(summary.next_fact, "next fact");
    if (truth(requireJsonObject(fact.payload, "next payload").text)) selected = [fact];
  }
  selected.sort((a, b) => {
    const x = a.presentation as [number, number, string, string], y = b.presentation as typeof x;
    return x[0] - y[0] || x[1] - y[1] || authorityUnicodeCompare(x[2], y[2]) || authorityUnicodeCompare(x[3], y[3]);
  });
  const selection = requireJsonObject(request.selection, "selection");
  const encoded = (key: string) => (pythonFieldIsList(source, key) ? summaryRows(source[key]) : [])
    .map(row => ({...materializeQuotaRow(row, profile), payload: row, display_source: {record: row}}));
  return {...request, ...(summary.materialize_resume === true ? {resume: materializeResumePlanningRequest(source,
      requireJsonObject(request.resume_options, "resume options"))} : {}),
    summary_request: {...summary, legacy}, selection: {...selection, items: selected,
    ...(materialize ? {active_items: encoded("active_next_action_items"), active_executable_items: encoded("active_next_action_executable_items")} : {}),
    source_open_count: legacy ? get(source, "open", get(source, "open_count", selected.length)) : get(source, "open_count", selected.length)}};
}

/** Complete presentation decisions stay here. Copies are explicit transport
 * references to values the old Python projection retained from its input. */
export function composeQuotaSummary(request: JsonObject, planning: JsonObject): JsonObject {
  const input = requireJsonObject(request.summary_request, "summary request");
  const source = decodeTodoReadSource(requireJsonObject(input.source, "summary source"));
  const legacy = input.legacy === true;
  const selection = requireJsonObject(request.selection, "selection");
  const agent = String(selection.agent_id ?? "").trim();
  const scoped = selection.user_gate_scope === true;
  const lanes = requireJsonObject(planning.lanes, "lanes");
  const lane = (name: string): JsonObject[] => {
    const value = lanes[name];
    if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`quota summary lane missing: ${name}`);
    return value.map(row => requireJsonObject(row, "quota summary lane row"));
  };
  const resume = requireJsonObject(planning.resume_planning, "resume");
  const capacity = summaryObject(resume.capacity_fields) ? resume.capacity_fields : {};
  const value = {...source, ...capacity};
  const copies: {source: Path; target: Path}[] = [];
  const refs = new Map<object, Path>();
  const integers = new Map<object, string>();
  const integerCopies: {value: string; target: Path}[] = [];
  const integer = (value: string): JsonObject => {const marker = {}; integers.set(marker, value); return marker;};
  const ref = (path: Path): JsonObject => {const marker = {}; refs.set(marker, path); return marker;};
  const field = (key: string, fallback: any = null): any => Object.hasOwn(capacity, key) ? capacity[key] :
    Object.hasOwn(source, key) ? ref([key]) : fallback;
  const sourceCountKey = legacy && Object.hasOwn(source, "open") ? "open" : "open_count";
  const count = lanes.open_count;
  const summary: JsonObject = {
    schema_version: field("schema_version"),
    source_section: legacy ? truth(value.source_section) ? field("source_section") : "project_asset" : field("source_section"),
    total_count: field(legacy && Object.hasOwn(value, "total") ? "total" : "total_count"),
    open_count: count, work_counts: planning.work_counts,
    done_count: field(legacy && Object.hasOwn(value, "done") ? "done" : "done_count"),
    ...(!legacy ? {deferred_count: field("deferred_count"), source_completeness: planning.source_completeness} : {}),
    first_open_items: lane("display_open_items").slice(0, 3), first_executable_items: lane("executable_items").slice(0, 3),
    ...(!legacy ? {gate_open_items: lanes.gate_items} : {}),
    monitor_open_items: lanes.monitor_items, monitor_due_count: lane("monitor_due_items").length,
    monitor_due_items: lane("monitor_due_items").slice(0, 1), watch_only_monitor_count: lane("watch_only_monitor_items").length,
    watch_only_monitor_due_count: lane("watch_only_monitor_due_items").length,
    watch_only_monitor_due_items: lane("watch_only_monitor_due_items").slice(0, 1),
    non_watch_only_monitor_due_items: lane("non_watch_only_monitor_due_items").slice(0, 1),
    monitor_capability_blocked_due_count: lane("monitor_capability_blocked_due_items").length,
    monitor_capability_blocked_due_items: lanes.monitor_capability_blocked_due_items,
    ...(!legacy ? {monitor_schedule_gap_count: lane("monitor_schedule_gap_items").length,
      monitor_schedule_gap_items: lane("monitor_schedule_gap_items").slice(0, 1)} : {}),
    active_next_action_items: lanes.active_next_action_items,
    active_next_action_executable_items: lanes.active_next_action_executable_items,
    backlog_items: lane("display_open_items").slice(0, 8), executable_backlog_items: lane("executable_items").slice(0, 8),
  };
  const display = (items: JsonObject[]) => projectTodoSummaryDisplay({items: items.map(record => ({record})), strip_text: true});
  if (!legacy) {
    for (const key of ["goal_acceptance_contract", "advancement_frontier_revision_index"]) if (summaryObject(value[key])) summary[key] = field(key);
    if (lane("watch_only_monitor_items").length) summary.convergence_open_count = field("convergence_open_count");
    const recent = summaryRows(value.recent_completed_advancement_items).filter(row => !agent || readAgent(row.claimed_by) === agent);
    if (recent.length) summary.recent_completed_advancement_items = display(recent);
    if (Array.isArray(value.vision_wait_states)) summary.vision_wait_states = value.vision_wait_states.flatMap((row: any, index: number) =>
      summaryObject(row) && row.agent_id === agent ? [ref(["vision_wait_states", index])] : []);
    const blockers = lane("all_open_items").filter((row: JsonObject) => row.task_class === "blocker" &&
      String(row.status || "").trim().toLowerCase() === "blocked" && String(row.reason || "").trim());
    const current = blockers.filter((row: JsonObject) => agent && readAgent(row.claimed_by) === agent);
    if (blockers.length) summary.blocker_open_count = blockers.length;
    if (current.length) {summary.current_agent_blocker_count = current.length; summary.current_agent_blocker_items = current.slice(0, 2);}
    if (truth(planning.closure_intent)) {
      const closure = requireJsonObject(planning.closure_intent, "closure intent");
      summary.closure_intent = {...Object.fromEntries(Object.keys(source.closure_intent as JsonObject).map(key => [key, ref(["closure_intent", key])])), ...closure};
    }
  }
  for (const key of ["frontier_deadline", "blocked_retry_wait"]) if (truth(planning[key])) summary[key] = planning[key];
  const monitor = value.monitor_writeback;
  if (summaryObject(monitor) && monitor.supported === false) {
    const label = pythonFieldText(monitor, "source").trim();
    summary.monitor_writeback = {supported: false, ...(label ? {source: label} : {})};
  }
  Object.assign(summary, planning.claim_visibility, resume.deferred_lanes,
    ...(!legacy ? [resume.resume_blocked_lanes] : []), planning.handoff_lanes, planning.route_lanes);
  if (!legacy) Object.assign(summary, successionWarning(value, ref, input.integer_counts, integer));
  const claimed = scoped ? null : value.claimed_open_count;
  if (lane("claimed_open_items").length || truth(claimed)) {
    summary.claimed_open_count = truth(claimed) ? field("claimed_open_count") : lane("claimed_open_items").length;
    summary.unclaimed_open_count = !scoped && Object.hasOwn(value, "unclaimed_open_count") ? field("unclaimed_open_count") :
      Math.max(0, Math.trunc(Number(lanes.open_count || 0)) - lane("claimed_open_items").length);
  }
  if (truth(lanes.claim_scope)) summary.claim_scope = lanes.claim_scope;
  const allCount = () => field(sourceCountKey, lane("all_open_items").length);
  if (!legacy && scoped) summary.all_open_count = allCount();
  if (truth(lanes.agent_scope_filter)) {
    summary.agent_scope_filter = lanes.agent_scope_filter;
    if (legacy) summary.all_open_count = allCount();
    summary.other_agent_scoped_open_count = lane("other_agent_scoped_items").length;
    summary.other_agent_scoped_items = display(lane("other_agent_scoped_items").slice(0, 16));
  }
  if (scoped && lane("user_action_open_items").length) {
    summary.user_action_open_count = lane("user_action_open_items").length;
    summary.user_action_items = display(lane("user_action_open_items").slice(0, 16));
  }
  if (truth(lanes.user_action_agent_scope_filter)) {
    summary.user_action_agent_scope_filter = lanes.user_action_agent_scope_filter;
    summary.other_agent_bound_user_action_open_count = lane("other_agent_bound_user_action_items").length;
    summary.other_agent_bound_user_action_items = display(lane("other_agent_bound_user_action_items").slice(0, 16));
  }
  function encode(value: any, path: Path): any {
    if (value && typeof value === "object" && integers.has(value)) {
      integerCopies.push({value: integers.get(value)!, target: path}); return null;
    }
    if (value && typeof value === "object" && refs.has(value)) {
      copies.push({source: refs.get(value)!, target: path}); return null;
    }
    if (Array.isArray(value)) return value.map((row, i) => encode(row, [...path, i]));
    if (summaryObject(value)) return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, encode(child, [...path, key])]));
    return value;
  }
  return {schema_version: "todo_quota_summary_wire_v1", summary: encode(summary, []), source_copies: copies, integer_copies: integerCopies};
}

function successionWarning(value: JsonObject, ref: (path: Path) => JsonObject,
  integerFacts: unknown, integer: (value: string) => JsonObject): JsonObject {
  const warning = summaryObject(value.todo_succession_warning) ? value.todo_succession_warning : {};
  const key = Array.isArray(warning.items) ? "todo_succession_warning" : "completed_without_successor_items";
  const source = key === "todo_succession_warning" ? warning.items : value[key];
  const items = (Array.isArray(source) ? source : []).flatMap((row: unknown, index: number) => {
    if (!summaryObject(row)) return [];
    const path: Path = key === "todo_succession_warning" ? [key, "items", index] : [key, index];
    const copy = (name: string) => Object.hasOwn(row, name) ? ref([...path, name]) : null;
    const projected: JsonObject = {index: copy("index"), text: copy("text")};
    for (const name of [
      "schema_version", "todo_id", "role", "status", "priority", "title", "archive_state", "source_section",
      "task_class", "action_kind", "task_domain", "task_repository", "continuation_policy", "required_write_scopes",
      "required_capabilities", "target_capabilities", "decision_scope", "required_decision_scopes", "claimed_by",
      "blocks_agent", "excluded_agents", "unblocks_todo_id", "resume_when", "resume_monitor_generation",
      "resume_condition", "resume_ready", "no_followup", "successor_todo_ids", "target_key", "cadence", "next_due_at",
      "expires_at", "last_checked_at", "result_hash", "consecutive_no_change", "material_change",
      "material_change_generation", "material_change_scope", "material_change_scope_generations",
      "max_no_change_before_replan", "route_continuation_replan_required",
      "route_continuation_reason", "route_id", "route_key", "completed_at", "updated_at", "superseded_by",
      "completion_continuation", "completion_recovery", "completion_turn_key", "done", "succession_tracked", "recommended_action",
    ]) if (row[name] != null) projected[name] = copy(name);
    for (const [name, normalized] of [["required_write_scopes", writeScopes(row.required_write_scopes)],
      ["decision_scope", readScope(row.decision_scope)], ["required_decision_scopes", readScopes(row.required_decision_scopes)]] as const) {
      if (normalized && (!Array.isArray(normalized) || normalized.length)) projected[name] = normalized;
      else delete projected[name];
    }
    projected.task_class = taskClass({...row, text: pythonFieldText(row, "text")});
    return [projected];
  }).slice(0, 8);
  const facts = summaryObject(integerFacts) ? integerFacts : {};
  const encoded = Object.hasOwn(warning, "count") ? facts.warning : facts.source;
  const numeric = typeof encoded === "string" ? BigInt(encoded) : BigInt(items.length);
  const positive = numeric > 0n;
  if (!positive && !items.length) return {};
  const count = numeric <= 0n ? 0 : numeric <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(numeric) : integer(String(numeric));
  const payload = {schema_version: Object.hasOwn(warning, "schema_version") ? ref(["todo_succession_warning", "schema_version"]) : "todo_succession_warning_v0",
    reason_code: Object.hasOwn(warning, "reason_code") ? ref(["todo_succession_warning", "reason_code"]) : "completed_advancement_without_successor", count, items,
    recommended_action: Object.hasOwn(warning, "recommended_action") ? ref(["todo_succession_warning", "recommended_action"]) : "Review remaining authorized Goal acceptance and the runnable frontier; follow the current quota settlement/replan contract. A summary warning does not authorize terminal action."};
  return {completed_without_successor_count: count, completed_without_successor_items: items, todo_succession_warning: payload};
}
