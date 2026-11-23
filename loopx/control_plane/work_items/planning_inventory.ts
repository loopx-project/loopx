import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
} from "../runtime_decode.ts";

import type { JsonObject } from "../effect_program.ts";

export const TODO_PLANNING_INVENTORY_SCHEMA_VERSION =
  "todo_planning_inventory_v0";
export const TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION =
  "todo_planning_inventory_request_v0";
export const TODO_PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION =
  "todo_planning_inventory_detail_v0";

const MAX_REQUEST_ITEMS_PER_LANE = 128;
const MAX_INVENTORY_ITEMS = 64;

export type PlanningState =
  | "selected"
  | "runnable"
  | "waiting"
  | "blocked"
  | "scheduled"
  | "context";

export type ClaimState = "current_agent" | "unclaimed" | "other_agent";

import {
  projectRelations, relation, ENFORCEMENT_BY_RELATION, PLANNING_TODO_ID as TODO_ID,
  type PlanningInventoryRelation, type PlanningRelationKind,
} from "./planning_relations.ts";

interface Candidate extends JsonObject {
  todo_id: string;
  text: string;
  priority?: string;
  status?: string;
  task_class?: string;
  action_kind?: string;
  claimed_by?: string;
  continuation_hint?: string;
  resume_when?: string;
  resume_ready?: boolean;
  next_due_at?: string;
  unblocks_todo_id?: string;
  successor_todo_ids: string[];
  superseded_by?: string;
  route_id?: string;
  route_key?: string;
  availability_reason?: string;
  required_capabilities?: string[];
  required_write_scopes?: string[];
  index?: number;
}

export interface PlanningInventoryItem extends Candidate {
  planning_state: PlanningState;
  claim_state: ClaimState;
  claim_required_before_work: boolean;
  runnable_candidate: boolean;
  unavailable_higher_priority: boolean;
}

export interface TodoPlanningInventory extends JsonObject {
  schema_version: typeof TODO_PLANNING_INVENTORY_SCHEMA_VERSION;
  goal_id: string;
  agent_id: string;
  selected_todo_id: string;
  items: PlanningInventoryItem[];
  relations: PlanningInventoryRelation[];
  completeness: JsonObject;
}

interface PlanningInventoryDetailItem extends JsonObject {
  todo_id: string;
  planning_state: PlanningState;
  claim_state: ClaimState;
  claim_required_before_work: boolean;
  runnable_candidate: boolean;
  unavailable_higher_priority: boolean;
  priority?: string;
  task_class?: string;
  action_kind?: string;
  claimed_by?: string;
  availability_reason?: string;
  resume_when?: string;
  next_due_at?: string;
}

function todoId(value: unknown, label: string): string {
  const normalized = requireNonEmptyString(value, label);
  if (!TODO_ID.test(normalized)) {
    throw new EffectRuntimeRequestError(`${label} must be a public Todo id`);
  }
  return normalized;
}

function optionalTodoId(value: unknown, label: string): string | undefined {
  const normalized = optionalNonEmptyString(value, label);
  return normalized === null ? undefined : todoId(normalized, label);
}

function candidate(value: unknown, label: string): Candidate {
  const raw = requireJsonObject(value, label);
  const decoded: Candidate = {
    todo_id: todoId(raw.todo_id, `${label}.todo_id`),
    text: requireNonEmptyString(raw.text, `${label}.text`),
    successor_todo_ids: [],
  };
  for (const field of [
    "priority",
    "status",
    "task_class",
    "action_kind",
    "claimed_by",
    "continuation_hint",
    "resume_when",
    "next_due_at",
    "route_id",
    "route_key",
    "availability_reason",
  ] as const) {
    const normalized = optionalNonEmptyString(raw[field], `${label}.${field}`);
    if (normalized !== null) decoded[field] = normalized;
  }
  for (const field of ["unblocks_todo_id", "superseded_by"] as const) {
    const normalized = optionalTodoId(raw[field], `${label}.${field}`);
    if (normalized !== undefined) decoded[field] = normalized;
  }
  if (raw.successor_todo_ids !== null && raw.successor_todo_ids !== undefined) {
    decoded.successor_todo_ids = requireStringArray(
      raw.successor_todo_ids,
      `${label}.successor_todo_ids`,
    ).map((item, index) => todoId(item, `${label}.successor_todo_ids[${index}]`));
  }
  if (raw.required_capabilities !== null && raw.required_capabilities !== undefined) {
    decoded.required_capabilities = requireStringArray(
      raw.required_capabilities,
      `${label}.required_capabilities`,
    );
  }
  if (raw.required_write_scopes !== null && raw.required_write_scopes !== undefined) {
    const scopes = requireStringArray(
      raw.required_write_scopes,
      `${label}.required_write_scopes`,
    );
    if (raw.include_write_scopes === true) decoded.required_write_scopes = scopes;
  }
  if (raw.resume_ready !== null && raw.resume_ready !== undefined) {
    if (typeof raw.resume_ready !== "boolean") {
      throw new EffectRuntimeRequestError(`${label}.resume_ready must be a boolean`);
    }
    decoded.resume_ready = raw.resume_ready;
  }
  if (raw.index !== null && raw.index !== undefined) {
    decoded.index = requireInteger(raw.index, `${label}.index`);
  }
  return decoded;
}

function mergeCandidate(existing: Candidate | undefined, update: Candidate): Candidate {
  if (!existing) return update;
  return {
    ...update,
    ...existing,
    successor_todo_ids: [...new Set([
      ...existing.successor_todo_ids,
      ...update.successor_todo_ids,
    ])],
    index: existing.index ?? update.index,
  };
}

function boundedLane(value: unknown): { values: unknown[]; omitted: number } {
  const raw = Array.isArray(value) ? value : [];
  return {
    values: raw.slice(0, MAX_REQUEST_ITEMS_PER_LANE),
    omitted: Math.max(0, raw.length - MAX_REQUEST_ITEMS_PER_LANE),
  };
}

function claimState(item: Candidate, agentId: string): ClaimState {
  if (!item.claimed_by) return "unclaimed";
  return item.claimed_by === agentId ? "current_agent" : "other_agent";
}

function planningState(
  item: Candidate,
  selectedTodoId: string,
  runnableTodoIds: ReadonlySet<string>,
): PlanningState {
  if (item.todo_id === selectedTodoId) return "selected";
  if (runnableTodoIds.has(item.todo_id)) return "runnable";
  if (item.status === "blocked") return "blocked";
  if (item.resume_when && item.resume_ready !== true) return "waiting";
  if (item.status === "deferred") return "waiting";
  if (item.task_class === "continuous_monitor" && item.next_due_at) {
    return "scheduled";
  }
  return "context";
}


function inventoryItem(
  item: Candidate,
  options: {
    agentId: string;
    selectedTodoId: string;
    runnableTodoIds: ReadonlySet<string>;
    unavailableTodoIds: ReadonlySet<string>;
  },
): PlanningInventoryItem {
  const state = planningState(
    item,
    options.selectedTodoId,
    options.runnableTodoIds,
  );
  const claim = claimState(item, options.agentId);
  return {
    ...item,
    planning_state: state,
    claim_state: claim,
    claim_required_before_work:
      (state === "selected" || state === "runnable") && claim === "unclaimed",
    runnable_candidate: options.runnableTodoIds.has(item.todo_id),
    unavailable_higher_priority: options.unavailableTodoIds.has(item.todo_id),
  };
}

function requireBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be a boolean`);
  }
  return value;
}

function planningInventoryItem(value: unknown, label: string): PlanningInventoryItem {
  const raw = requireJsonObject(value, label);
  const decoded = candidate(raw, label);
  const state = requireNonEmptyString(raw.planning_state, `${label}.planning_state`);
  if (![
    "selected",
    "runnable",
    "waiting",
    "blocked",
    "scheduled",
    "context",
  ].includes(state)) {
    throw new EffectRuntimeRequestError(`${label}.planning_state is invalid`);
  }
  const claim = requireNonEmptyString(raw.claim_state, `${label}.claim_state`);
  if (!["current_agent", "unclaimed", "other_agent"].includes(claim)) {
    throw new EffectRuntimeRequestError(`${label}.claim_state is invalid`);
  }
  return {
    ...decoded,
    planning_state: state as PlanningState,
    claim_state: claim as ClaimState,
    claim_required_before_work: requireBoolean(
      raw.claim_required_before_work,
      `${label}.claim_required_before_work`,
    ),
    runnable_candidate: requireBoolean(
      raw.runnable_candidate,
      `${label}.runnable_candidate`,
    ),
    unavailable_higher_priority: requireBoolean(
      raw.unavailable_higher_priority,
      `${label}.unavailable_higher_priority`,
    ),
  };
}

function planningInventoryRelation(
  value: unknown,
  label: string,
): PlanningInventoryRelation {
  const raw = requireJsonObject(value, label);
  const kind = requireNonEmptyString(raw.relation, `${label}.relation`);
  if (!(kind in ENFORCEMENT_BY_RELATION)) {
    throw new EffectRuntimeRequestError(`${label}.relation is invalid`);
  }
  const relationKind = kind as PlanningRelationKind;
  const expectedEnforcement = ENFORCEMENT_BY_RELATION[relationKind];
  if (raw.enforcement !== expectedEnforcement) {
    throw new EffectRuntimeRequestError(
      `${label}.enforcement must be ${expectedEnforcement} for ${relationKind}`,
    );
  }
  return relation(
    todoId(raw.from_todo_id, `${label}.from_todo_id`),
    requireNonEmptyString(raw.to_ref, `${label}.to_ref`),
    relationKind,
  );
}

function requirePlanningInventory(value: unknown, label: string): TodoPlanningInventory {
  const raw = requireJsonObject(value, label);
  if (raw.schema_version !== TODO_PLANNING_INVENTORY_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `${label}.schema_version must be ${TODO_PLANNING_INVENTORY_SCHEMA_VERSION}`,
    );
  }
  const items = Array.isArray(raw.items)
    ? raw.items.map((item, index) =>
      planningInventoryItem(item, `${label}.items[${index}]`)
    )
    : [];
  const relations = Array.isArray(raw.relations)
    ? raw.relations.map((item, index) =>
      planningInventoryRelation(item, `${label}.relations[${index}]`)
    )
    : [];
  return {
    schema_version: TODO_PLANNING_INVENTORY_SCHEMA_VERSION,
    goal_id: requireNonEmptyString(raw.goal_id, `${label}.goal_id`),
    agent_id: requireNonEmptyString(raw.agent_id, `${label}.agent_id`),
    selected_todo_id: todoId(raw.selected_todo_id, `${label}.selected_todo_id`),
    items,
    relations,
    completeness: requireJsonObject(raw.completeness, `${label}.completeness`),
  };
}

export function decodeTodoPlanningInventory(value: unknown): TodoPlanningInventory {
  return requirePlanningInventory(value, "planning_inventory");
}

/**
 * Expose planning semantics without duplicating the full Todo rows carried by
 * the same cold-path packet. Callers follow item_detail_ref for text, scopes,
 * capabilities, and other operational fields.
 */
export function projectTodoPlanningInventoryDetail(value: unknown): JsonObject {
  const inventory = decodeTodoPlanningInventory(value);
  const items: PlanningInventoryDetailItem[] = inventory.items.map((item) => {
    const projected: PlanningInventoryDetailItem = {
      todo_id: item.todo_id,
      planning_state: item.planning_state,
      claim_state: item.claim_state,
      claim_required_before_work: item.claim_required_before_work,
      runnable_candidate: item.runnable_candidate,
      unavailable_higher_priority: item.unavailable_higher_priority,
    };
    for (const field of [
      "priority",
      "task_class",
      "action_kind",
      "claimed_by",
      "availability_reason",
      "resume_when",
      "next_due_at",
    ] as const) {
      if (item[field] !== undefined) projected[field] = item[field];
    }
    return projected;
  });
  return {
    schema_version: TODO_PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION,
    goal_id: inventory.goal_id,
    agent_id: inventory.agent_id,
    selected_todo_id: inventory.selected_todo_id,
    items,
    relations: inventory.relations,
    completeness: inventory.completeness,
    item_detail_ref: "$.agent_todo_summary",
  };
}

/** Normalize one agent-scoped Todo planning source before any presentation lens. */
export function projectTodoPlanningInventory(value: unknown): TodoPlanningInventory {
  const request = requireJsonObject(value, "planning_inventory_request");
  if (request.schema_version !== TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `planning_inventory_request.schema_version must be ${TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION}`,
    );
  }
  const goalId = requireNonEmptyString(request.goal_id, "planning_inventory_request.goal_id");
  const agentId = requireNonEmptyString(request.agent_id, "planning_inventory_request.agent_id");
  const selected = candidate(request.selected_todo, "planning_inventory_request.selected_todo");
  const sourceContextCount = requireInteger(
    request.source_context_todo_count,
    "planning_inventory_request.source_context_todo_count",
  );
  if (sourceContextCount < 0) {
    throw new EffectRuntimeRequestError(
      "planning_inventory_request.source_context_todo_count must be non-negative",
    );
  }

  const sourceLane = boundedLane(request.source_items);
  const runnableLane = boundedLane(request.runnable_candidates);
  const unavailableLane = boundedLane(request.unavailable_higher_priority);
  const decodedSource = sourceLane.values.map((item, index) =>
    candidate(item, `planning_inventory_request.source_items[${index}]`)
  );
  const decodedRunnable = runnableLane.values.map((item, index) =>
    candidate(item, `planning_inventory_request.runnable_candidates[${index}]`)
  );
  const decodedUnavailable = unavailableLane.values.map((item, index) =>
    candidate(item, `planning_inventory_request.unavailable_higher_priority[${index}]`)
  );
  const runnableTodoIds = new Set(decodedRunnable.map((item) => item.todo_id));
  const unavailableTodoIds = new Set(decodedUnavailable.map((item) => item.todo_id));
  const sourceTodoIds = new Set(decodedSource.map((item) => item.todo_id));

  const byId = new Map<string, Candidate>();
  const merge = (item: Candidate) => {
    byId.set(item.todo_id, mergeCandidate(byId.get(item.todo_id), item));
  };
  merge(selected);
  decodedRunnable.forEach(merge);
  decodedUnavailable.forEach(merge);
  decodedSource.forEach(merge);

  const allCandidates = [...byId.values()];
  const boundedCandidates = allCandidates.slice(0, MAX_INVENTORY_ITEMS);
  const omittedInventoryItems = Math.max(0, allCandidates.length - boundedCandidates.length);
  const items = boundedCandidates.map((item) =>
    inventoryItem(item, {
      agentId,
      selectedTodoId: selected.todo_id,
      runnableTodoIds,
      unavailableTodoIds,
    })
  );
  const representedSourceCount = items.filter((item) => sourceTodoIds.has(item.todo_id)).length;
  const sourceUnrepresented = Math.max(0, sourceContextCount - representedSourceCount);
  const requestOverflow = sourceLane.omitted + runnableLane.omitted + unavailableLane.omitted;
  const relations = projectRelations(items);

  return {
    schema_version: TODO_PLANNING_INVENTORY_SCHEMA_VERSION,
    goal_id: goalId,
    agent_id: agentId,
    selected_todo_id: selected.todo_id,
    items,
    relations,
    completeness: {
      schema_version: "todo_planning_inventory_completeness_v0",
      source_context_todo_count: sourceContextCount,
      source_input_todo_count: sourceTodoIds.size,
      represented_source_todo_count: representedSourceCount,
      source_unrepresented_todo_count: sourceUnrepresented,
      omitted_request_item_count: requestOverflow,
      omitted_inventory_item_count: omittedInventoryItems,
      complete: sourceUnrepresented === 0 && requestOverflow === 0 && omittedInventoryItems === 0,
    },
  };
}
