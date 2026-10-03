import type { JsonObject } from "../effect_program.ts";
import {
  DELIVERY_BOUNDARY_RESULT_SCHEMA,
  DELIVERY_CONTINUITY_RESULT_SCHEMA,
  DELIVERY_ROUTING_REQUEST_SCHEMA,
  DELIVERY_ROUTING_RESULT_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";
export {
  DELIVERY_BOUNDARY_RESULT_SCHEMA,
  DELIVERY_CONTINUITY_RESULT_SCHEMA,
  DELIVERY_ROUTING_REQUEST_SCHEMA,
  DELIVERY_ROUTING_RESULT_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  decodeOptionalMaterialDeliveryOutcome,
  type MaterialDeliveryOutcome,
} from "../work_items/delivery_outcome.ts";

export const DELIVERY_BOUNDARIES = [
  "in_flight_continuation",
  "semantic_closeout",
] as const;

export const DELIVERY_CONTINUITY_PREEMPTIONS = [
  "heartbeat_receipt",
  "blocking_work_lane",
  "autonomous_replan",
  "control_repair",
  "delivery_not_allowed",
] as const;

export type DeliveryBoundary = (typeof DELIVERY_BOUNDARIES)[number];
export type DeliveryContinuityPreemption =
  (typeof DELIVERY_CONTINUITY_PREEMPTIONS)[number];

type TodoStatus = "open" | "done" | "blocked" | "deferred";
const TODO_STATUSES = ["open", "done", "blocked", "deferred"] as const;

export interface DeliveryContinuityTodo {
  todo_id: string;
  status: TodoStatus;
  task_class: string;
  claimed_by: string | null;
  actionable: boolean;
  capability_ready: boolean;
  replan_obligation_id?: string | null;
}

interface DeliveryContinuityFacts {
  agent_id: string;
  previous_todo_id: string | null;
  previous_delivery_outcome: MaterialDeliveryOutcome | null;
  current_todo: DeliveryContinuityTodo | null;
  preemptions: readonly DeliveryContinuityPreemption[];
}

export type DeliveryContinuityDecision =
  | "resume_in_flight"
  | "release_for_reselection"
  | "preempt";

export type DeliveryContinuityReason =
  | DeliveryContinuityPreemption
  | "no_previous_todo"
  | "previous_delivery_not_progress"
  | "current_todo_missing"
  | "todo_identity_changed"
  | "todo_not_open"
  | "todo_not_advancement"
  | "todo_not_actionable"
  | "todo_capability_blocked"
  | "todo_claimed_by_other_agent"
  | "same_open_todo_after_progress";

export type DeliveryContinuityResult = JsonObject & {
  schema_version: typeof DELIVERY_CONTINUITY_RESULT_SCHEMA;
  decision: DeliveryContinuityDecision;
  reason: DeliveryContinuityReason;
  todo_id: string | null;
  delivery_boundary: DeliveryBoundary;
};

interface DeliveryBoundaryFacts {
  agent_id: string;
  current_todo: DeliveryContinuityTodo | null;
  preemptions: readonly DeliveryContinuityPreemption[];
}

export type DeliveryBoundaryReason =
  | DeliveryContinuityPreemption
  | "no_selected_todo"
  | "todo_not_open"
  | "todo_not_advancement"
  | "todo_not_actionable"
  | "todo_capability_blocked"
  | "todo_claimed_by_other_agent"
  | "open_advancement_todo";

export type DeliveryBoundaryResult = JsonObject & {
  schema_version: typeof DELIVERY_BOUNDARY_RESULT_SCHEMA;
  delivery_boundary: DeliveryBoundary;
  reason: DeliveryBoundaryReason;
  todo_id: string | null;
};

export type DeliveryRoutingSelection = "continuity" | "fallback" | "replan" | "none";

interface DeliveryRoutingRequest {
  schema_version: typeof DELIVERY_ROUTING_REQUEST_SCHEMA;
  agent_id: string;
  previous_todo_id: string | null;
  previous_delivery_outcome: MaterialDeliveryOutcome | null;
  continuity_todo: DeliveryContinuityTodo | null;
  fallback_todo: DeliveryContinuityTodo | null;
  replan_todo: DeliveryContinuityTodo | null;
  latest_replan_ack: JsonObject | null;
  preemptions: readonly DeliveryContinuityPreemption[];
}

export type DeliveryRoutingResult = JsonObject & {
  schema_version: typeof DELIVERY_ROUTING_RESULT_SCHEMA;
  selection: DeliveryRoutingSelection;
  continuity: DeliveryContinuityResult | null;
  boundary: DeliveryBoundaryResult | null;
};

function todoStatus(value: unknown, label: string): TodoStatus {
  return requireStringLiteral(
    value,
    TODO_STATUSES,
    label,
    `${label} is unsupported`,
  );
}

function preemptions(value: unknown): DeliveryContinuityPreemption[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("preemptions must be an array");
  }
  const result: DeliveryContinuityPreemption[] = [];
  for (const item of value) {
    if (
      typeof item !== "string" ||
      !DELIVERY_CONTINUITY_PREEMPTIONS.some((candidate) => candidate === item)
    ) {
      throw new EffectRuntimeRequestError("preemptions contains an unsupported reason");
    }
    const reason = item as DeliveryContinuityPreemption;
    if (!result.includes(reason)) result.push(reason);
  }
  return result;
}

function currentTodo(
  value: unknown,
  label = "current_todo",
): DeliveryContinuityTodo | null {
  if (value === null || value === undefined) return null;
  const todo = requireJsonObject(value, label);
  return {
    todo_id: requireNonEmptyString(todo.todo_id, `${label}.todo_id`),
    status: todoStatus(todo.status, `${label}.status`),
    task_class: requireNonEmptyString(todo.task_class, `${label}.task_class`),
    claimed_by: optionalNonEmptyString(todo.claimed_by, `${label}.claimed_by`),
    actionable: requireBoolean(todo.actionable, `${label}.actionable`),
    capability_ready: requireBoolean(
      todo.capability_ready,
      `${label}.capability_ready`,
    ),
    replan_obligation_id: optionalNonEmptyString(todo.replan_obligation_id, `${label}.replan_obligation_id`),
  };
}

function result(
  decision: DeliveryContinuityDecision,
  reason: DeliveryContinuityReason,
  todoId: string | null,
): DeliveryContinuityResult {
  return {
    schema_version: DELIVERY_CONTINUITY_RESULT_SCHEMA,
    decision,
    reason,
    todo_id: todoId,
    delivery_boundary: decision === "resume_in_flight"
      ? "in_flight_continuation"
      : "semantic_closeout",
  };
}

function resolveDeliveryContinuity(
  request: DeliveryContinuityFacts,
): DeliveryContinuityResult {
  const todoId = request.previous_todo_id;
  const preemption = request.preemptions[0];
  if (preemption !== undefined) {
    return result("preempt", preemption, todoId);
  }
  if (todoId === null) {
    return result("release_for_reselection", "no_previous_todo", null);
  }
  if (request.previous_delivery_outcome !== "outcome_progress") {
    return result(
      "release_for_reselection",
      "previous_delivery_not_progress",
      todoId,
    );
  }
  const todo = request.current_todo;
  if (todo === null) {
    return result("release_for_reselection", "current_todo_missing", todoId);
  }
  if (todo.todo_id !== todoId) {
    return result("release_for_reselection", "todo_identity_changed", todoId);
  }
  if (todo.status !== "open") {
    return result("release_for_reselection", "todo_not_open", todoId);
  }
  if (todo.task_class !== "advancement_task") {
    return result("release_for_reselection", "todo_not_advancement", todoId);
  }
  if (!todo.actionable) {
    return result("release_for_reselection", "todo_not_actionable", todoId);
  }
  if (!todo.capability_ready) {
    return result("release_for_reselection", "todo_capability_blocked", todoId);
  }
  if (todo.claimed_by !== null && todo.claimed_by !== request.agent_id) {
    return result(
      "release_for_reselection",
      "todo_claimed_by_other_agent",
      todoId,
    );
  }
  return result(
    "resume_in_flight",
    "same_open_todo_after_progress",
    todoId,
  );
}

function boundaryResult(
  deliveryBoundary: DeliveryBoundary,
  reason: DeliveryBoundaryReason,
  todoId: string | null,
): DeliveryBoundaryResult {
  return {
    schema_version: DELIVERY_BOUNDARY_RESULT_SCHEMA,
    delivery_boundary: deliveryBoundary,
    reason,
    todo_id: todoId,
  };
}

function resolveDeliveryBoundary(
  request: DeliveryBoundaryFacts,
): DeliveryBoundaryResult {
  const todo = request.current_todo;
  const todoId = todo?.todo_id ?? null;
  const preemption = request.preemptions[0];
  if (preemption !== undefined) {
    return boundaryResult("semantic_closeout", preemption, todoId);
  }
  if (todo === null) {
    return boundaryResult("semantic_closeout", "no_selected_todo", null);
  }
  if (todo.status !== "open") {
    return boundaryResult("semantic_closeout", "todo_not_open", todoId);
  }
  if (todo.task_class !== "advancement_task") {
    return boundaryResult("semantic_closeout", "todo_not_advancement", todoId);
  }
  if (!todo.actionable) {
    return boundaryResult("semantic_closeout", "todo_not_actionable", todoId);
  }
  if (!todo.capability_ready) {
    return boundaryResult("semantic_closeout", "todo_capability_blocked", todoId);
  }
  if (todo.claimed_by !== null && todo.claimed_by !== request.agent_id) {
    return boundaryResult(
      "semantic_closeout",
      "todo_claimed_by_other_agent",
      todoId,
    );
  }
  return boundaryResult(
    "in_flight_continuation",
    "open_advancement_todo",
    todoId,
  );
}

function decodeDeliveryRoutingRequest(value: unknown): DeliveryRoutingRequest {
  const request = requireJsonObject(value, "turn.delivery_route params");
  if (request.schema_version !== DELIVERY_ROUTING_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Delivery routing request schema mismatch");
  }
  return {
    schema_version: DELIVERY_ROUTING_REQUEST_SCHEMA,
    agent_id: requireNonEmptyString(request.agent_id, "agent_id"),
    previous_todo_id: optionalNonEmptyString(
      request.previous_todo_id,
      "previous_todo_id",
    ),
    previous_delivery_outcome: decodeOptionalMaterialDeliveryOutcome(
      request.previous_delivery_outcome,
      "previous_delivery_outcome",
    ),
    continuity_todo: currentTodo(request.continuity_todo, "continuity_todo"),
    fallback_todo: currentTodo(request.fallback_todo, "fallback_todo"),
    replan_todo: currentTodo(request.replan_todo, "replan_todo"),
    latest_replan_ack: request.latest_replan_ack == null ? null
      : requireJsonObject(request.latest_replan_ack, "latest_replan_ack"),
    preemptions: preemptions(request.preemptions),
  };
}

/** A durable accepted successor biases fresh planning, never delivery authority.
 * The latest scoped ACK replaces older preferences; current admission and exact
 * origin must still agree. Missing historical facts preserve the old route.
 */
function prefersReplanSuccessor(request: DeliveryRoutingRequest): boolean {
  const ack = request.latest_replan_ack;
  const todo = request.replan_todo;
  if (
    !ack || !todo || request.preemptions.length ||
    todo.claimed_by !== request.agent_id ||
    ack.schema_version !== "autonomous_replan_ack_v0" || ack.recorded !== true ||
    ack.agent_id !== request.agent_id
  ) return false;
  const raw = ack.semantic_delta;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
  const delta = raw as JsonObject;
  if (
    delta.schema_version !== "replan_semantic_delta_v0" || delta.accepted !== true ||
    !Array.isArray(delta.outcomes) || !delta.outcomes.includes("new_runnable_successor") ||
    delta.successor_todo_id !== todo.todo_id || !todo.replan_obligation_id ||
    (delta.successor_origin_obligation_id ?? delta.obligation_id) !== todo.replan_obligation_id
  ) return false;
  return resolveDeliveryBoundary({
    agent_id: request.agent_id,
    current_todo: todo,
    preemptions: request.preemptions,
  }).delivery_boundary === "in_flight_continuation";
}

/** Resolve one quota delivery route behind one runtime request/response. */
export function evaluateDeliveryRoute(value: unknown): DeliveryRoutingResult {
  const request = decodeDeliveryRoutingRequest(value);
  const continuity = request.previous_todo_id === null
    ? null
    : resolveDeliveryContinuity({
      agent_id: request.agent_id,
      previous_todo_id: request.previous_todo_id,
      previous_delivery_outcome: request.previous_delivery_outcome,
      current_todo: request.continuity_todo,
      preemptions: request.preemptions,
    });
  const selection: DeliveryRoutingSelection =
    prefersReplanSuccessor(request)
      ? "replan"
      : continuity?.decision === "resume_in_flight"
      ? "continuity"
      : request.fallback_todo === null
      ? "none"
      : "fallback";
  const selectedTodo = selection === "continuity"
    ? request.continuity_todo
    : selection === "replan"
    ? request.replan_todo
    : selection === "fallback"
    ? request.fallback_todo
    : null;
  const boundary = selectedTodo === null
    ? null
    : resolveDeliveryBoundary({
      agent_id: request.agent_id,
      current_todo: selectedTodo,
      preemptions: request.preemptions,
    });
  return {
    schema_version: DELIVERY_ROUTING_RESULT_SCHEMA,
    selection,
    continuity,
    boundary,
  };
}
