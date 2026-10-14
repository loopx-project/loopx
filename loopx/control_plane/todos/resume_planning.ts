import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {countDeferredTodoWork, readDeferredTodoWorkCounts} from "./summary_lanes.ts";
import {
  jsonObject, requireJsonObject, requireBoolean, requireInteger,
  requireStringArray, optionalNonEmptyString,
} from "../runtime_decode.ts";
import {
  diagnoseTodoResumeCondition, type ResumeConditionDiagnosis,
  evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
} from "./resume_condition.ts";

export const RESUME_PLANNING_REQUEST = "todo_resume_planning_request_v0";
export const RESUME_PLANNING_RESULT = "todo_resume_planning_v0";
const DEFERRED_POLICY = "quota may wake the current peer only for ready deferred todos " +
  "claimed by that agent or unclaimed; other-agent deferred todos remain " +
  "diagnostic visibility and executor-excluded todos remain visible but non-selectable";
const MONITOR_POLICY = "open advancement todos gated by todo_done:<continuous_monitor> must " +
  "project as successor replan/state repair instead of quiet monitor wait";
const SOURCE_KEYS = ["items", "backlog_items", "first_open_items", "deferred_items",
  "deferred_resume_candidates", "resume_blocked_items", "monitor_open_items",
  "current_agent_claimed_monitor_items", "claimed_monitor_open_items"] as const;
type SourceKey = typeof SOURCE_KEYS[number];
type ClaimLane = "current_agent" | "unclaimed" | "other_agent" | "executor_excluded_self";

/** Codec facts are separate from policy. Payload fields remain lossless while
 * comparisons use the existing reader's normalized values and stable order. */
interface Item {
  payload: JsonObject;
  id: string | null;
  status: string | null;
  claim: string | null;
  excluded: readonly string[];
  resume: string | null;
  done: boolean;
  ready: boolean | null;
  readyTruthy: boolean;
  priority: number;
  index: number;
  targetId: string | null;
  targetStatus: string | null;
  targetClass: string;
  diagnosis?: ResumeConditionDiagnosis;
}

function item(value: unknown): Item {
  const raw = requireJsonObject(value, "resume planning item");
  const payload = requireJsonObject(raw.payload, "payload");
  if (typeof payload.text !== "string" || typeof payload.task_class !== "string") {
    throw new EffectRuntimeRequestError("resume planning payload requires text and task_class");
  }
  const optional = (key: string) => optionalNonEmptyString(raw[key], key);
  return {
    payload, id: optional("id"), status: optional("status"), claim: optional("claim"),
    excluded: requireStringArray(raw.excluded, "excluded"), resume: optional("resume"),
    done: requireBoolean(raw.done, "done"),
    ready: raw.ready === null ? null : requireBoolean(raw.ready, "ready"),
    readyTruthy: requireBoolean(raw.ready_truthy, "ready_truthy"),
    priority: requireInteger(raw.priority, "priority"), index: requireInteger(raw.index, "index"),
    targetId: optional("target_id"), targetStatus: optional("target_status"),
    targetClass: optional("target_class") ?? "advancement_task",
  };
}

function ordered(items: readonly Item[]): Item[] {
  // Stable sort retains persisted/source-lane order for equal public keys.
  return [...items].sort((a, b) => a.priority - b.priority || a.index - b.index);
}

function itemIdentity(entry: Item): string {
  // A canonical Todo id owns identity across compact/display and lossless
  // candidate projections. Fall back to the legacy presentation identity only
  // for rows that predate typed Todo ids.
  return entry.id
    ? JSON.stringify(["todo", entry.id])
    : JSON.stringify(["projection", entry.payload.text, entry.payload.index]);
}

function unique(items: readonly Item[]): Item[] {
  const seen = new Set<string>();
  return items.filter((entry) => {
    const key = itemIdentity(entry);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function deferred(source: readonly Item[]): Item[] {
  return ordered(source.filter((entry) => entry.payload.text && entry.status === "deferred")
    .map((entry) => ({ ...entry, payload: {
      ...entry.payload,
      ...(entry.resume ? { resume_when: entry.resume } : {}),
      ...(entry.payload.resume_ready === undefined ? {} : { resume_ready: entry.readyTruthy }),
    } })));
}

function blocked(source: readonly Item[]): Item[] {
  return ordered(unique(source.filter((entry) => entry.payload.text && !entry.done &&
    entry.resume !== null && entry.ready === false)));
}

function monitorBlocked(source: readonly Item[]): Item[] {
  return ordered(unique(source.filter((entry) => entry.payload.task_class === "advancement_task" &&
    entry.diagnosis?.state === "invalid" && entry.diagnosis.reason === "monitor_completion_requires_replan"
  ).map((entry) => ({ ...entry, payload: {
    ...entry.payload, ...(entry.targetId ? { blocking_monitor_todo_id: entry.targetId } : {}),
  } }))));
}

function claimLane(entry: Item, agent: string): ClaimLane {
  if (entry.excluded.includes(agent)) return "executor_excluded_self";
  if (!entry.claim) return "unclaimed";
  return entry.claim === agent ? "current_agent" : "other_agent";
}

function payloads(items: readonly Item[], limit?: number): JsonObject[] {
  return (limit === undefined ? items : items.slice(0, limit)).map((entry) => entry.payload);
}

function claimLanes(items: readonly Item[], agent: string, limit: number,
  family: "deferred_resume" | "monitor_blocked_resume"): JsonObject {
  const result: JsonObject = {};
  for (const lane of ["current_agent", "unclaimed", "other_agent", "executor_excluded_self"] as const) {
    const selected = items.filter((entry) => claimLane(entry, agent) === lane);
    result[`${lane}_${family}_candidates`] = payloads(selected, limit);
    result[`${lane}_${family}_count`] = selected.length;
  }
  result[`${family}_selection_policy`] = family === "deferred_resume" ? DEFERRED_POLICY : MONITOR_POLICY;
  return result;
}

function successorWaits(resumeBlocked: readonly Item[], deferredItems: readonly Item[], agent: string | null): Item[] {
  if (!agent) return [];
  return ordered(unique([...resumeBlocked, ...deferredItems.filter((entry) => entry.payload.resume_ready === false)])
    .filter((entry) => {
      const lane = claimLane(entry, agent);
      return entry.payload.task_class === "advancement_task" &&
        (lane === "current_agent" || lane === "unclaimed") && entry.resume !== null &&
        jsonObject(entry.payload.resume_condition)?.satisfied === false &&
        entry.diagnosis?.state === "pending" && entry.diagnosis.kind !== "monitor_changed";
    }).map((entry) => ({ ...entry, payload: {
      ...entry.payload, resume_when: entry.resume, resume_ready: false,
    } }))).sort((a, b) => Number(b.claim === agent) - Number(a.claim === agent));
}

function dependencyCycleReplan(items: readonly Item[], agent: string | null): JsonObject | null {
  if (!agent) return null;
  const candidates = ordered(unique(items)).filter(entry => {
    const lane = claimLane(entry, agent);
    return entry.payload.role === "agent" && entry.payload.task_class === "advancement_task" &&
      ["open", "deferred"].includes(entry.status ?? "") &&
      (entry.payload.archive_state == null || entry.payload.archive_state === "active") &&
      (lane === "current_agent" || lane === "unclaimed") &&
      entry.diagnosis?.state === "invalid" && entry.diagnosis.reason === "dependency_cycle";
  });
  if (!candidates.length) return null;
  const first = candidates[0];
  return {count: candidates.length, candidates: payloads(candidates, 3),
    reason: `Todo ${first.id} depends on an unfinished completion cycle; waiting cannot resolve this plan.`,
    recommended_action: "Inspect the reported dependency_cycle_todo_ids and their current owners. Replan an obsolete dependency only through the authorized Todo lifecycle with fresh owner/lease proof. If another owner must change an edge, report that concrete blocker. Do not clear a valid dependency, mark work done, or change another owner's Todo merely to unlock execution.",
    spend_policy: "no spend for cycle diagnosis; replan credit requires validated authorized writeback"};
}

function resolveCapacity(items: readonly Item[], source: readonly Item[], capabilities: string[]): Item[] {
  const result = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: payloads(items).filter((entry) => entry.todo_id),
    source_items: payloads([...source, ...items]).filter((entry) => entry.todo_id),
    available_capabilities: capabilities, kinds: ["capacity_available"], rollout_events: [],
  });
  const conditions = new Map<string, JsonObject>();
  for (const row of result.conditions as JsonObject[]) {
    conditions.set(String(row.todo_id), requireJsonObject(row.condition, "resume condition"));
  }
  return items.map((entry) => {
    const condition = conditions.get(entry.id ?? "");
    if (!condition) return entry;
    const ready = condition.satisfied === true;
    return { ...entry, ready, readyTruthy: ready, payload: {
      ...entry.payload, resume_condition: condition, resume_ready: ready,
    } };
  });
}

function decodeSources(value: unknown): Record<SourceKey, Item[]> {
  const rawSources = requireJsonObject(value, "sources");
  const sources = {} as Record<SourceKey, Item[]>;
  for (const key of SOURCE_KEYS) {
    if (!Array.isArray(rawSources[key])) throw new EffectRuntimeRequestError(`${key} must be an array`);
    sources[key] = rawSources[key].map(item);
  }
  return sources;
}

function deferredPlan(sources: Record<SourceKey, Item[]>, capabilities: unknown, observed: Item[]) {
  let deferredItems = deferred(sources.deferred_items.length ? sources.deferred_items : sources.items);
  const projectedCandidates = deferred(sources.deferred_resume_candidates);
  let candidates = projectedCandidates.filter((entry) => entry.payload.resume_ready === true);
  let capacityFields: JsonObject | null = null;
  let countSource = observed;
  if (capabilities !== null) {
    // deferred_items is a bounded presentation lane while
    // deferred_resume_candidates retains semantically selected rows that may
    // fall outside that display window. Re-evaluate their union so supplying
    // runtime capabilities cannot erase an already projected ready successor.
    // Put the explicit candidate lane first so its lossless payload wins when
    // the same Todo is also present in a compact display lane.
    const displayIdentities = new Set(deferredItems.map(itemIdentity));
    const selected = ordered(unique([
      ...projectedCandidates,
      ...deferredItems,
    ]));
    const reevaluated = resolveCapacity(unique([...selected, ...observed]), sources.items,
      requireStringArray(capabilities, "available_capabilities"));
    const selectedIds = new Set(selected.map(itemIdentity));
    const selectedReevaluated = reevaluated.filter(entry => selectedIds.has(itemIdentity(entry)));
    deferredItems = selectedReevaluated.filter((entry) => displayIdentities.has(itemIdentity(entry)));
    candidates = selectedReevaluated.filter((entry) => entry.payload.resume_ready === true);
    countSource = reevaluated;
    capacityFields = { deferred_items: payloads(deferredItems), deferred_resume_candidates: payloads(candidates) };
    // Existing summary readers treat an empty explicit deferred lane as absent,
    // including after capacity resolution; retain that compatibility fallback.
    if (!deferredItems.length) deferredItems = deferred(sources.items);
  }
  return { deferredItems, candidates, capacityFields, countSource };
}

function monitorIds(sources: Record<SourceKey, Item[]>): Set<string> {
  return new Set(["monitor_open_items", "current_agent_claimed_monitor_items",
    "claimed_monitor_open_items", "items", "backlog_items", "first_open_items"]
    .flatMap((key) => sources[key as SourceKey])
    .filter((entry) => entry.id && entry.payload.task_class === "continuous_monitor")
    .map((entry) => entry.id!));
}

function diagnoseSources(sources: Record<SourceKey, Item[]>): void {
  const monitors = monitorIds(sources);
  // Compatibility belongs here once, not in every agent-scope consumer. Old
  // compact conditions may omit kind/class while the same snapshot has them.
  for (const key of SOURCE_KEYS) sources[key] = sources[key].map((entry) => {
    const condition = jsonObject(entry.payload.resume_condition);
    if (!condition || !entry.resume) return entry;
    const facts = {
      ...condition, resume_when: entry.resume,
      target_todo_id: entry.targetId,
      target_status: entry.targetStatus,
      target_task_class: monitors.has(entry.targetId ?? "") ? "continuous_monitor" : entry.targetClass,
    };
    const diagnosis = diagnoseTodoResumeCondition(facts, entry.id);
    if (diagnosis.state !== "invalid") return { ...entry, diagnosis };
    return { ...entry, diagnosis, ready: false, readyTruthy: false, payload: {
      ...entry.payload, resume_ready: false, resume_condition: {
        ...condition, satisfied: false, invalid_state: diagnosis.reason,
        availability_reason: "resume_condition_invalid",
      },
    } };
  });
}

interface DisplayOptions {
  agent: string | null;
  limit: number;
  hasCount: boolean;
  hasVisibleCount: boolean;
  count: JsonObject[string];
}

function deferredVisibility(items: Item[], candidates: Item[], options: DisplayOptions): JsonObject {
  const { agent, limit, hasCount, hasVisibleCount, count } = options;
  return items.length || candidates.length || hasVisibleCount ? {
    deferred_count: hasCount ? count : items.length,
    deferred_visibility_limit: limit, deferred_items: payloads(items, limit),
    deferred_resume_candidates: payloads(candidates, limit),
    ...(agent ? claimLanes(candidates, agent, limit, "deferred_resume") : {}),
  } : {};
}

function blockedVisibility(resumeBlocked: Item[], monitorItems: Item[], options: DisplayOptions): JsonObject {
  const { agent, limit } = options;
  return resumeBlocked.length ? {
    resume_blocked_count: resumeBlocked.length, resume_blocked_items: payloads(resumeBlocked, limit),
    ...(monitorItems.length ? {
      monitor_blocked_resume_count: monitorItems.length,
      monitor_blocked_resume_candidates: payloads(monitorItems, limit),
      ...(agent ? claimLanes(monitorItems, agent, limit, "monitor_blocked_resume") : {}),
    } : {}),
  } : {};
}

/** One read-only decision for quota lanes, replan candidates and exact waits.
 * No mutation, claim, lease grant, monitor poll, or recovery effect is emitted. */
export function projectTodoResumePlanning(value: unknown): JsonObject {
  const request = requireJsonObject(value, "resume planning request");
  if (request.schema_version !== RESUME_PLANNING_REQUEST) {
    throw new EffectRuntimeRequestError("resume planning schema mismatch");
  }
  const sources = decodeSources(request.sources);
  diagnoseSources(sources);
  const display: DisplayOptions = {
    agent: optionalNonEmptyString(request.agent_id, "agent_id"),
    limit: requireInteger(request.item_limit, "item_limit"),
    hasCount: requireBoolean(request.has_deferred_count, "has_deferred_count"),
    hasVisibleCount: requireBoolean(request.has_visible_deferred_count, "has_visible_deferred_count"),
    count: request.deferred_count,
  };
  // A producer's complete counts survive presentation truncation. Re-evaluate
  // available rows only when current capabilities can change their readiness;
  // any unseen remainder then remains explicitly unknown.
  const sourceRows = [
    ...sources.deferred_resume_candidates, ...sources.deferred_items, ...sources.items,
  ];
  const identities = new Map<string, Item>();
  const conflicting = new Set<string>();
  for (const row of sourceRows) {
    const id = itemIdentity(row), previous = identities.get(id);
    if (previous && (row.status !== previous.status || row.ready !== previous.ready || row.claim !== previous.claim)) conflicting.add(id);
    if (!previous) identities.set(id, row);
  }
  const observed = deferred([...identities.values()]).map(row => conflicting.has(itemIdentity(row))
    ? {...row, ready: null, claim: null} : row);
  const {deferredItems, candidates, capacityFields, countSource} =
    deferredPlan(sources, request.available_capabilities, observed);
  const counts = request.deferred_work_counts != null && request.available_capabilities === null
    ? readDeferredTodoWorkCounts(request.deferred_work_counts, request.deferred_count)
    : countDeferredTodoWork(countSource.map(row => ({
      ready: conflicting.has(itemIdentity(row)) ? null : row.ready, claim: row.claim,
    })),
      request.has_deferred_count === true ? request.deferred_count : null);
  const resumeBlocked = blocked(sources.resume_blocked_items.length ? sources.resume_blocked_items
    : [...sources.items, ...sources.backlog_items, ...sources.first_open_items]);
  const monitorItems = monitorBlocked(resumeBlocked);
  return {
    schema_version: RESUME_PLANNING_RESULT,
    deferred_lanes: {...deferredVisibility(deferredItems, candidates, display), deferred_work_counts: counts},
    resume_blocked_lanes: blockedVisibility(resumeBlocked, monitorItems, display),
    capacity_fields: capacityFields,
    deferred_items: payloads(deferredItems), monitor_blocked_items: payloads(monitorItems),
    blocked_successor_items: payloads(successorWaits(resumeBlocked, deferredItems, display.agent)),
    dependency_cycle_replan: dependencyCycleReplan([...resumeBlocked, ...deferredItems], display.agent),
  };
}
