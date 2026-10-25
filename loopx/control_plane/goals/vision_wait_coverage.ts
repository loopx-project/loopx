import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { optionalNonEmptyString, requireInteger, requireJsonObject, requireStringArray } from "../runtime_decode.ts";
import { projectTodoResumePlanning } from "../todos/resume_planning.ts";

const WAIT_STATE_SCHEMA = "goal_vision_wait_state_v0";
const VISION_GAP = "vision_acceptance_gap";
const CAUSAL_ID_KEYS = ["todo_id", "current_todo_id", "blocked_todo_id", "completed_todo_id", "successor_todo_id",
  "vision_todo_ids", "lineage_todo_ids", "successor_todo_ids", "completed_todo_ids"] as const;

/** Every causal obligation needs its own witness; shared prerequisites do not cover sibling work. */
export function projectVisionWaitCoverage(requestPayload: unknown): JsonObject {
  const request = requireJsonObject(requestPayload, "vision wait coverage");
  const roots = requireStringArray(request.causal_todo_ids, "causal_todo_ids");
  const waits = new Set(requireStringArray(request.waiting_todo_ids, "waiting_todo_ids"));
  const blockers = new Set(requireStringArray(request.blocker_todo_ids, "blocker_todo_ids"));
  if (!Array.isArray(request.edges)) throw new EffectRuntimeRequestError("edges must be an array");
  const graph = new Map<string, Set<string>>();
  for (const edge of request.edges) {
    const pair = requireStringArray(edge, "lineage edge");
    if (pair.length !== 2) throw new EffectRuntimeRequestError("lineage edge must have two ids");
    const targets = graph.get(pair[0]) ?? new Set<string>();
    targets.add(pair[1]);
    graph.set(pair[0], targets);
  }
  const witnesses = new Set<string>();
  const uncovered = new Set<string>();
  for (const root of roots) {
    const seen = new Set<string>();
    const pending = [root];
    let covered = false;
    while (pending.length) {
      const todoId = pending.pop()!;
      if (seen.has(todoId)) continue;
      seen.add(todoId);
      if (waits.has(todoId) || blockers.has(todoId)) { witnesses.add(todoId); covered = true; }
      for (const next of graph.get(todoId) ?? []) pending.push(next);
    }
    if (!covered) uncovered.add(root);
  }
  return {schema_version: "vision_wait_coverage_v0", covered: roots.length > 0 && uncovered.size === 0,
    witness_todo_ids: [...witnesses].sort(), uncovered_todo_ids: [...uncovered].sort()};
}

function normalizedTodoId(candidate: unknown): string | null {
  if (typeof candidate !== "string") return null;
  const id = candidate.trim().toLowerCase();
  return /^todo_[a-z0-9_-]{3,64}$/.test(id) ? id : null;
}

function visionGapRows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((gap): gap is JsonObject =>
    typeof gap === "object" && gap !== null && !Array.isArray(gap)) : [];
}

function causalTodoIds(gaps: readonly JsonObject[]): string[] | null {
  const causal = new Set<string>();
  for (const gap of gaps) {
    let linked = false;
    for (const key of CAUSAL_ID_KEYS) {
      const ids = Array.isArray(gap[key]) ? gap[key] as unknown[] : [gap[key]];
      for (const candidate of ids) {
        const id = normalizedTodoId(candidate);
        if (id) { causal.add(id); linked = true; }
      }
    }
    if (!linked) return null;
  }
  return [...causal].sort();
}

function cachedWaitProof(summary: JsonObject, agentId: string | null, causalIds: string[]): JsonObject | null {
  for (const proof of Array.isArray(summary.vision_wait_states) ? summary.vision_wait_states : []) {
    if (typeof proof !== "object" || proof === null || Array.isArray(proof)) continue;
    const cachedWait = proof as JsonObject;
    if (cachedWait.schema_version === WAIT_STATE_SCHEMA && cachedWait.state === "waiting" &&
        cachedWait.agent_id === (agentId ?? null) && JSON.stringify(cachedWait.causal_todo_ids) === JSON.stringify(causalIds)) {
      return cachedWait;
    }
  }
  return null;
}

function normalizedAgentId(candidate: unknown): string | null {
  if (typeof candidate !== "string") return null;
  const id = candidate.trim().toLowerCase().replaceAll(" ", "-");
  return /^[a-z][a-z0-9_.:@-]{0,79}$/.test(id) ? id : null;
}

function currentAgentBlockers(rows: unknown, agentId: string | null): JsonObject[] {
  if (!Array.isArray(rows) || !agentId) return [];
  return rows.filter((rawRow): rawRow is JsonObject => {
    if (typeof rawRow !== "object" || rawRow === null || Array.isArray(rawRow)) return false;
    const blocker = rawRow as JsonObject;
    const excludedRows = Array.isArray(blocker.excluded_agents) ? blocker.excluded_agents
      : typeof blocker.excluded_agents === "string" ? blocker.excluded_agents.split(",") : [];
    return blocker.task_class === "blocker" && blocker.status === "blocked" &&
      normalizedAgentId(blocker.claimed_by) === agentId && !excludedRows.some(id => normalizedAgentId(id) === agentId);
  });
}

function waitLineageEdges(rows: unknown): string[][] {
  const edges = new Set<string>();
  for (const rawRow of Array.isArray(rows) ? rows : []) {
    if (typeof rawRow !== "object" || rawRow === null || Array.isArray(rawRow)) continue;
    const lineageRow = rawRow as JsonObject;
    const id = normalizedTodoId(lineageRow.todo_id);
    if (!id) continue;
    for (const next of Array.isArray(lineageRow.successor_todo_ids) ? lineageRow.successor_todo_ids : []) {
      const successor = normalizedTodoId(next);
      if (successor) edges.add(JSON.stringify([id, successor]));
    }
    const condition = typeof lineageRow.resume_condition === "object" && lineageRow.resume_condition !== null
      ? lineageRow.resume_condition as JsonObject : {};
    const target = condition.satisfied === false ? normalizedTodoId(condition.target_todo_id) : null;
    if (target) edges.add(JSON.stringify([target, id]));
  }
  return [...edges].map(edge => JSON.parse(edge) as string[]);
}

function projectWaitCoverage(causalIds: string[], candidates: JsonObject[], blockers: JsonObject[], lineageRows: unknown): JsonObject {
  return projectVisionWaitCoverage({causal_todo_ids: causalIds,
    waiting_todo_ids: candidates.map(candidate => candidate.todo_id).filter((id): id is string => typeof id === "string"),
    blocker_todo_ids: blockers.map(blocker => blocker.todo_id).filter((id): id is string => typeof id === "string"),
    edges: waitLineageEdges(lineageRows)});
}

function selectedWaitItems(blockers: JsonObject[], candidates: JsonObject[], witnesses: Set<string>) {
  const selectedBlockers = blockers.filter(blocker => witnesses.has(String(blocker.todo_id)));
  const selectedCandidates = selectedBlockers.length ? [] : candidates.filter(candidate => witnesses.has(String(candidate.todo_id)));
  return {waitItems: selectedCandidates.length ? selectedCandidates : selectedBlockers, automatic: selectedCandidates.length > 0};
}

function compactResumeCondition(resumeCondition: unknown): JsonObject {
  const condition = typeof resumeCondition === "object" && resumeCondition !== null && !Array.isArray(resumeCondition)
    ? resumeCondition as JsonObject : {};
  const keys = ["schema_version", "resume_when", "satisfied", "kind", "target", "target_todo_id", "target_status",
    "target_archive_state", "target_source_section", "target_task_class", "target_claimed_by", "target_next_due_at",
    "capability", "provider", "scheduled_for", "evaluated_at", "clock_provider", "material_change",
    "material_change_generation", "baseline_generation", "invalid_state", "target_expires_at"];
  const compact: JsonObject = Object.fromEntries(keys.filter(key => key in condition).map(key => [key, condition[key]]));
  if (condition.kind === "resume_at") compact.resume_receipt = condition.resume_receipt ?? null;
  compact.satisfied = false;
  return compact;
}

function automaticResumeFields(selected: JsonObject): JsonObject {
  const fields: JsonObject = {};
  for (const key of ["resume_when", "resume_monitor_generation"] as const) {
    if (selected[key] !== undefined && selected[key] !== null) fields[key] = selected[key];
  }
  fields.resume_condition = compactResumeCondition(selected.resume_condition);
  return fields;
}

function blockerReasonField(selectedTodo: JsonObject): JsonObject {
  return typeof selectedTodo.reason === "string" && selectedTodo.reason.trim()
    ? {blocker_reason: selectedTodo.reason.trim()} : {};
}

function waitStatePayload(input: {causalIds: string[]; gapCount: number; selectedTodo: JsonObject; waitItems: JsonObject[];
  agentId: string | null; automatic: boolean}): JsonObject {
  const {causalIds, gapCount, selectedTodo, waitItems, agentId, automatic} = input;
  const payload: JsonObject = {
    schema_version: WAIT_STATE_SCHEMA, state: "waiting",
    reason_code: automatic ? "exact_blocked_successor" : "current_agent_blocker",
    agent_id: agentId, waiting_todo_count: waitItems.length,
    waiting_todo_ids: waitItems.slice(0, 5).map(candidate => normalizedTodoId(candidate.todo_id)).filter((id): id is string => id !== null),
    selected_todo_id: normalizedTodoId(selectedTodo.todo_id), selected_todo_status: selectedTodo.status ?? null,
    selected_todo_priority: selectedTodo.priority ?? null, selected_todo_claimed_by: selectedTodo.claimed_by ?? null,
    ...(automatic ? automaticResumeFields(selectedTodo) : blockerReasonField(selectedTodo)),
    causal_todo_ids: causalIds, deferred_acceptance_gap_count: gapCount,
    deferred_acceptance_gap_kinds: [VISION_GAP], automatic_resume: automatic,
    resume_behavior: automatic
      ? "when resume_ready becomes true, restore ordinary open-todo or deferred-successor routing without closing the active vision"
      : "re-evaluate the active vision when the blocker is resolved or superseded, or when runnable scoped advancement appears",
  };
  return Object.fromEntries(Object.entries(payload).filter(([, field]) => field !== null));
}

function projectVisionWaitFromFacts(input: {request: JsonObject; gaps: JsonObject[]; causalIds: string[]}): JsonObject | null {
  const {request, gaps, causalIds} = input;
  const agentId = optionalNonEmptyString(request.agent_id, "agent_id");
  const summary = request.agent_todo_summary === null ? {} : requireJsonObject(request.agent_todo_summary, "agent_todo_summary");
  const cachedWait = cachedWaitProof(summary, agentId, causalIds);
  if (cachedWait) return cachedWait;
  const resumePlan = projectTodoResumePlanning(requireJsonObject(request.resume_planning_request, "resume_planning_request"));
  const blockers = currentAgentBlockers(summary.current_agent_blocker_items, agentId);
  const candidates = Array.isArray(resumePlan.blocked_successor_items) ? resumePlan.blocked_successor_items as JsonObject[] : [];
  const lineageRows = Array.isArray(request.lineage_source_items) ? request.lineage_source_items : candidates;
  const coverage = projectWaitCoverage(causalIds, candidates, blockers, lineageRows);
  if (coverage.covered !== true) return null;
  const waitSelection = selectedWaitItems(blockers, candidates, new Set(coverage.witness_todo_ids as string[]));
  if (!waitSelection.waitItems.length) return null;
  return waitStatePayload({causalIds, gapCount: gaps.length, selectedTodo: waitSelection.waitItems[0],
    waitItems: waitSelection.waitItems, agentId, automatic: waitSelection.automatic});
}

/** Composes resume planning and causal witness evaluation in one runtime call. */
export function projectGoalVisionWaitState(requestValue: unknown): JsonObject | null {
  const request = requireJsonObject(requestValue, "vision wait state");
  const gaps = visionGapRows(request.acceptance_gaps);
  const selectableCount = requireInteger(request.selectable_advancement_count, "selectable_advancement_count");
  if (!gaps.length || gaps.some(gap => gap.kind !== VISION_GAP) || selectableCount !== 0) return null;
  const causalIds = causalTodoIds(gaps);
  return causalIds ? projectVisionWaitFromFacts({request, gaps, causalIds}) : null;
}
