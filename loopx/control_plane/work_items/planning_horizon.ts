import {todoPriorityRank as priorityRank} from "../todos/priority.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import {
  decodeTodoPlanningInventory,
  type PlanningInventoryItem,
  type PlanningState,
} from "./planning_inventory.ts";
import { relationKey, todoRef, type PlanningInventoryRelation } from "./planning_relations.ts";

import type { JsonObject } from "../effect_program.ts";
import { projectCoreGoal } from "./replan_context.ts";

export const PLANNING_HORIZON_SCHEMA_VERSION = "quota_planning_horizon_v0";
export const PLANNING_HORIZON_REQUEST_SCHEMA_VERSION =
  "quota_planning_horizon_request_v1";

const MAX_PROJECTED_ITEMS = 5;
const MAX_PROJECTED_RELATIONS = 8;
const MAX_PROJECTED_ACCEPTANCE_GAPS = 2;
const ITEM_TEXT_LIMIT = 180;
const CONTEXT_TEXT_LIMIT = 140;
const ACCEPTANCE_TEXT_LIMIT = 220;

function compactText(value: unknown, limit: number): { text: string; truncated: boolean } {
  const normalized = requireNonEmptyString(value, "planning horizon text")
    .trim()
    .replace(/\s+/g, " ");
  return {
    text: normalized.slice(0, limit),
    truncated: normalized.length > limit,
  };
}

function connectedDistances(
  selectedTodoId: string,
  values: readonly PlanningInventoryRelation[],
): Map<string, number> {
  const adjacency = new Map<string, Set<string>>();
  const connect = (left: string, right: string) => {
    const neighbors = adjacency.get(left) ?? new Set<string>();
    neighbors.add(right);
    adjacency.set(left, neighbors);
  };
  for (const relation of values) {
    const target = todoRef(relation.to_ref);
    if (!target) continue;
    connect(relation.from_todo_id, target);
    connect(target, relation.from_todo_id);
  }
  const distances = new Map<string, number>([[selectedTodoId, 0]]);
  const queue = [selectedTodoId];
  while (queue.length > 0) {
    const current = queue.shift()!;
    const nextDistance = (distances.get(current) ?? 0) + 1;
    for (const neighbor of adjacency.get(current) ?? []) {
      if (distances.has(neighbor)) continue;
      distances.set(neighbor, nextDistance);
      queue.push(neighbor);
    }
  }
  return distances;
}

function contextReasons(
  item: PlanningInventoryItem,
  state: PlanningState,
  selectedPriority: number,
  distance: number | undefined,
): string[] {
  const reasons: string[] = [];
  if (distance !== undefined && distance > 0) reasons.push("related_to_selected");
  if (priorityRank(item.priority) < selectedPriority) {
    reasons.push("higher_priority_than_selected");
  }
  if (state === "runnable") reasons.push("runnable_alternative");
  if (state === "waiting") reasons.push("pending_resume_condition");
  if (state === "blocked") reasons.push("explicit_blocker");
  if (state === "scheduled") reasons.push("scheduled_observation");
  return reasons;
}

function compactAcceptanceGap(value: unknown, index: number): JsonObject & { truncated: boolean } {
  const raw = requireJsonObject(value, `acceptance_gaps[${index}]`);
  const compact: JsonObject & { truncated: boolean } = {
    kind: requireNonEmptyString(raw.kind, `acceptance_gaps[${index}].kind`),
    truncated: false,
  };
  for (const field of [
    "source",
    "acceptance_summary",
    "replan_trigger_summary",
    "advancement_policy",
  ] as const) {
    const normalized = optionalNonEmptyString(raw[field], `acceptance_gaps[${index}].${field}`);
    if (normalized === null) continue;
    const bounded = compactText(
      normalized,
      field === "source" || field === "advancement_policy"
        ? CONTEXT_TEXT_LIMIT
        : ACCEPTANCE_TEXT_LIMIT,
    );
    compact[field] = bounded.text;
    compact.truncated ||= bounded.truncated;
  }
  return compact;
}

/**
 * Project the bounded context an agent needs to reason beyond one local action.
 *
 * The reducer is deliberately read-only. It orders and bounds existing typed
 * facts, but selected_todo/action_portfolio remain the only dispatch authority.
 */
export function projectQuotaPlanningHorizon(value: unknown): JsonObject | null {
  const request = requireJsonObject(value, "planning_horizon_request");
  if (request.schema_version !== PLANNING_HORIZON_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `planning_horizon_request.schema_version must be ${PLANNING_HORIZON_REQUEST_SCHEMA_VERSION}`,
    );
  }
  const inventory = decodeTodoPlanningInventory(request.planning_inventory);
  const goalId = inventory.goal_id;
  const agentId = inventory.agent_id;
  const selected = inventory.items.find(
    (item) => item.todo_id === inventory.selected_todo_id,
  );
  if (!selected) {
    throw new EffectRuntimeRequestError(
      "planning_horizon_request.planning_inventory must include selected_todo_id",
    );
  }
  const sourceRelations = inventory.relations;
  const distances = connectedDistances(selected.todo_id, sourceRelations);
  const selectedPriority = priorityRank(selected.priority);
  const strategicContextStates = new Set<PlanningState>([
    "waiting",
    "blocked",
    "scheduled",
  ]);
  const ordered = [...inventory.items].sort((left, right) => {
    const leftDistance = distances.get(left.todo_id);
    const rightDistance = distances.get(right.todo_id);
    const bucket = (item: PlanningInventoryItem, distance: number | undefined) => {
      if (item.todo_id === selected.todo_id) return 0;
      if (distance !== undefined) return 1;
      if (strategicContextStates.has(item.planning_state)) {
        return 2;
      }
      if (priorityRank(item.priority) < selectedPriority) return 3;
      if (item.runnable_candidate) return 4;
      return 5;
    };
    return (
      bucket(left, leftDistance) - bucket(right, rightDistance) ||
      (leftDistance ?? 1_000) - (rightDistance ?? 1_000) ||
      priorityRank(left.priority) - priorityRank(right.priority) ||
      (left.index ?? 1_000_000) - (right.index ?? 1_000_000) ||
      left.todo_id.localeCompare(right.todo_id)
    );
  });
  const projectedItems = ordered.slice(0, MAX_PROJECTED_ITEMS).map((item) => {
    const state = item.planning_state;
    const text = compactText(item.text, ITEM_TEXT_LIMIT);
    const projected: JsonObject = {
      todo_id: item.todo_id,
      text: text.text,
      planning_state: state,
      claim_state: item.claim_state,
      context_reasons: contextReasons(
        item,
        state,
        selectedPriority,
        distances.get(item.todo_id),
      ),
    };
    if (item.claim_required_before_work) {
      projected.claim_required_before_work = true;
    }
    for (const field of ["priority", "action_kind"] as const) {
      if (item[field] !== undefined) projected[field] = item[field];
    }
    if (item.claimed_by) projected.claimed_by = item.claimed_by;
    if (item.task_class && item.task_class !== "advancement_task") {
      projected.task_class = item.task_class;
    }
    if (item.continuation_hint) {
      const hint = compactText(item.continuation_hint, CONTEXT_TEXT_LIMIT);
      projected.continuation_hint = hint.text;
      if (hint.truncated) projected.context_truncated = true;
    }
    if (text.truncated) projected.context_truncated = true;
    return projected;
  });
  const projectedIds = new Set(projectedItems.map((item) => String(item.todo_id)));
  const relevantRelations = sourceRelations.filter((relation) => {
    const target = todoRef(relation.to_ref);
    return projectedIds.has(relation.from_todo_id) || (target !== null && projectedIds.has(target));
  }).sort((left, right) => {
    const relationRank = (relation: PlanningInventoryRelation) => ({
      successor: 0,
      unblocks: 1,
      superseded_by: 2,
      resumes_when: 3,
      routes_via: 4,
    })[relation.relation];
    const distance = (relation: PlanningInventoryRelation) => {
      const target = todoRef(relation.to_ref);
      return Math.min(
        distances.get(relation.from_todo_id) ?? 1_000,
        target ? distances.get(target) ?? 1_000 : 1_000,
      );
    };
    return relationRank(left) - relationRank(right) ||
      distance(left) - distance(right) || relationKey(left).localeCompare(relationKey(right));
  });
  const projectedRelations = relevantRelations.slice(0, MAX_PROJECTED_RELATIONS);
  const rawGaps = Array.isArray(request.acceptance_gaps) ? request.acceptance_gaps : [];
  const compactGaps = rawGaps.map((gap, index) =>
    compactAcceptanceGap(gap, index)
  );
  const projectedGaps = compactGaps
    .slice(0, MAX_PROJECTED_ACCEPTANCE_GAPS)
    .map(({ truncated: _truncated, ...gap }) => gap);
  const inventoryCompleteness = inventory.completeness;
  const sourceContextCount = Number(inventoryCompleteness.source_context_todo_count ?? 0);
  const sourceUnrepresented = Number(
    inventoryCompleteness.source_unrepresented_todo_count ?? 0,
  );
  const omittedItems = Math.max(0, inventory.items.length - projectedItems.length);
  const omittedRelations = Math.max(0, sourceRelations.length - projectedRelations.length);
  const omittedGaps = Math.max(0, compactGaps.length - projectedGaps.length);
  const compactFieldTruncationCount =
    projectedItems.filter((item) => item.context_truncated === true).length +
    compactGaps.slice(0, MAX_PROJECTED_ACCEPTANCE_GAPS).filter((gap) => gap.truncated).length;
  const addsStrategicContext = sourceRelations.length > 0 ||
    compactGaps.length > 0 ||
    ordered.some((item) =>
      item.todo_id !== selected.todo_id &&
      strategicContextStates.has(item.planning_state)
    );
  const core = request.goal_facts === undefined ? null : projectCoreGoal(request.goal_facts);
  if (!addsStrategicContext && core === null) {
    return null;
  }
  return {
    schema_version: PLANNING_HORIZON_SCHEMA_VERSION,
    mode: "read_only",
    goal_id: goalId,
    agent_id: agentId,
    selected_todo_id: selected.todo_id,
    ...(core === null ? {} : {core_goal: core}),
    selection_contract: {
      selected_todo_authority: "$.selected_todo",
      action_choice_authority: "$.action_portfolio",
      horizon_changes_selection: false,
      explicit_selection_required_for_other_work: true,
    },
    work_items: projectedItems,
    relations: projectedRelations,
    acceptance_gaps: projectedGaps,
    attention_todo_ids: projectedItems
      .filter((item) =>
        item.todo_id !== selected.todo_id &&
        Array.isArray(item.context_reasons) &&
        item.context_reasons.length > 0
      )
      .slice(0, 3)
      .map((item) => item.todo_id),
    completeness: {
      schema_version: "quota_planning_horizon_completeness_v0",
      source_context_todo_count: sourceContextCount,
      candidate_input_count: inventory.items.length,
      source_unrepresented_todo_count: sourceUnrepresented,
      planning_inventory_complete: inventoryCompleteness.complete === true,
      omitted_candidate_todo_count: omittedItems,
      omitted_relation_count: omittedRelations,
      omitted_acceptance_gap_count: omittedGaps,
      compact_field_truncation_count: compactFieldTruncationCount,
      complete: inventoryCompleteness.complete === true && sourceUnrepresented === 0 && omittedItems === 0 &&
        omittedRelations === 0 && omittedGaps === 0 && compactFieldTruncationCount === 0,
    },
    detail_refs: {
      selected_todo: {
        schema_version: "todo_detail_ref_v0",
        goal_id: goalId,
        role: "agent",
        todo_id: selected.todo_id,
        projection: "todo_detail_cold_path_v0",
      },
      agent_todos: `quota should-run --goal-id ${goalId} --agent-id ${agentId} --include-detail agent-todos`,
      full_todo_list: `todo list --goal-id ${goalId} --role agent --status open --agent-id ${agentId}`,
      task_graph: "status --include-task-graph",
    },
  };
}
