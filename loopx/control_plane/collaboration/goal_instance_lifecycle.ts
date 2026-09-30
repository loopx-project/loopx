import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { assertNever, jsonObject } from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "../goals/goal_instance_identity.ts";

const SOURCE_SESSION_PROFILE_ID = "source_session_v1";

const OPERATIONS = [
  "request_create",
  "inbox_observe",
  "read_record",
  "receiver_decide",
  "artifact_link",
  "result_publish",
  "peer_return_observe",
  "peer_return_consume",
  "original_return_admit",
  "original_return_settle",
  "history_inspect",
] as const;

type CollaborationOperation = (typeof OPERATIONS)[number];

type WireGoalRef = Readonly<{
  goal_id: string;
  goal_instance_id: string;
}>;

type CollaborationLifecycleFacts = Readonly<{
  operation: CollaborationOperation;
  callerGoalRef: ExactGoalRef;
  currentGoalRef: ExactGoalRef;
  recordGoalRef: ExactGoalRef | null;
  routeGoalRef: ExactGoalRef | null;
  initialDeliveryProved: boolean;
}>;

export type CollaborationLifecycleDecision =
  | Readonly<{ kind: "legacy" }>
  | Readonly<{
    kind: "allow";
    mode: "current_instance" | "historical_result" | "historical_read";
    goal_ref: WireGoalRef;
  }>
  | Readonly<{
    kind: "omit";
    code: "different_goal_instance" | "legacy_unbound";
  }>
  | Readonly<{
    kind: "reject";
    code:
      | "unsupported_profile"
      | "missing_exact_goal_ref"
      | "stale_goal_instance"
      | "record_instance_mismatch"
      | "route_instance_mismatch"
      | "historical_mutation_forbidden"
      | "initial_delivery_unproved";
  }>;

function operation(value: unknown): CollaborationOperation {
  switch (value) {
    case "request_create":
    case "inbox_observe":
    case "read_record":
    case "receiver_decide":
    case "artifact_link":
    case "result_publish":
    case "peer_return_observe":
    case "peer_return_consume":
    case "original_return_admit":
    case "original_return_settle":
    case "history_inspect":
      return value;
    default:
      throw new EffectRuntimeRequestError(
        "collaboration lifecycle operation is unsupported",
      );
  }
}

function optionalGoalRef(value: unknown): ExactGoalRef | null {
  if (value === null) return null;
  const parsed = parseExactGoalRef(value);
  return parsed.kind === "parsed" ? parsed.value : null;
}

function goalRefsEqual(left: ExactGoalRef, right: ExactGoalRef): boolean {
  return left.goalId.value === right.goalId.value
    && left.goalInstanceId.value === right.goalInstanceId.value;
}

function wireGoalRef(value: ExactGoalRef): WireGoalRef {
  return {
    goal_id: value.goalId.value,
    goal_instance_id: value.goalInstanceId.value,
  };
}

/** Prove a persisted Inbox request, independently of the caller's answer.
 * IO adapters supply the committed entry, trusted route and current source
 * authority. A caller failure does not cancel separately delegated work.
 */
export function proveOriginalRequestDelivery(value: unknown): JsonObject {
  const facts = jsonObject(value);
  const request = jsonObject(facts?.request);
  const route = jsonObject(facts?.route);
  const turn = jsonObject(facts?.turn);
  if (!request || !route || !turn
      || typeof request.request_id !== "string"
      || !BARE_SHA256_PATTERN.test(request.request_id)
      || ["request_id", "goal_id", "agent_id", "source_id"].some((key) =>
        typeof request[key] !== "string" || !request[key]
        || request[key] !== route[key])) {
    return { kind: "unproved", reason: "request_route_mismatch" };
  }
  // Exact instances must agree; legacy requests remain explicitly unbound.
  const requestRef = optionalGoalRef(request.goal_ref);
  const routeRef = optionalGoalRef(route.goal_ref);
  if (request.goal_ref === undefined && route.goal_ref === undefined) {
    // The legacy route is still bound to this committed request and source.
  } else if (!requestRef || !routeRef || !goalRefsEqual(requestRef, routeRef)
      || requestRef.goalId.value !== request.goal_id) {
    return { kind: "unproved", reason: "request_route_mismatch" };
  }
  if (typeof route.client_turn_id !== "string" || !route.client_turn_id
      || route.client_turn_id !== turn.client_turn_id
      || facts?.authorized_source_id !== request.source_id) {
    return { kind: "unproved", reason: "source_mismatch" };
  }
  switch (turn.status) {
    case "completed": case "failed": case "timed_out": case "interrupted": break;
    default: return { kind: "unproved", reason: "originating_turn_unsettled" };
  }
  const receiptValue = turn.context_handoff_receipt;
  if (receiptValue !== null && receiptValue !== undefined) {
    const receipt = jsonObject(receiptValue);
    const receiptRef = optionalGoalRef(receipt?.goal_ref);
    if (!receipt || ["request_id", "goal_id", "agent_id"].some((key) => receipt[key] !== request[key])
        || (requestRef ? !receiptRef || !goalRefsEqual(requestRef, receiptRef)
          : receipt.goal_ref !== undefined)) {
      return { kind: "unproved", reason: "handoff_receipt_conflict" };
    }
    return { kind: "proved", basis: "committed_request_and_receipt" };
  }
  return { kind: "proved", basis: "committed_request" };
}

function isObservation(operation: CollaborationOperation): boolean {
  return operation === "inbox_observe"
    || operation === "peer_return_observe";
}

function requiresRoute(operation: CollaborationOperation): boolean {
  return operation === "result_publish"
    || operation === "peer_return_observe"
    || operation === "peer_return_consume"
    || operation === "original_return_admit"
    || operation === "original_return_settle";
}

function permitsHistorical(operation: CollaborationOperation): boolean {
  return operation === "result_publish"
    || operation === "original_return_admit"
    || operation === "original_return_settle"
    || operation === "history_inspect";
}

function lifecycleFacts(
  value: JsonObject,
  selectedOperation: CollaborationOperation,
): CollaborationLifecycleFacts | CollaborationLifecycleDecision {
  const callerGoalRef = optionalGoalRef(value.caller_goal_ref);
  const currentGoalRef = optionalGoalRef(value.current_goal_ref);
  if (callerGoalRef === null || currentGoalRef === null) {
    return { kind: "reject", code: "missing_exact_goal_ref" };
  }
  const recordGoalRef = optionalGoalRef(value.record_goal_ref);
  const routeGoalRef = optionalGoalRef(value.route_goal_ref);
  return {
    operation: selectedOperation,
    callerGoalRef,
    currentGoalRef,
    recordGoalRef,
    routeGoalRef,
    initialDeliveryProved: proveOriginalRequestDelivery(value.initial_delivery).kind === "proved",
  };
}

function decideExactLifecycle(
  facts: CollaborationLifecycleFacts,
): CollaborationLifecycleDecision {
  const {
    operation,
    callerGoalRef,
    currentGoalRef,
    recordGoalRef,
    routeGoalRef,
  } = facts;
  if (operation === "request_create") {
    return goalRefsEqual(callerGoalRef, currentGoalRef)
      ? {
        kind: "allow",
        mode: "current_instance",
        goal_ref: wireGoalRef(callerGoalRef),
      }
      : { kind: "reject", code: "stale_goal_instance" };
  }

  if (recordGoalRef === null) {
    return isObservation(operation)
      ? { kind: "omit", code: "legacy_unbound" }
      : { kind: "reject", code: "missing_exact_goal_ref" };
  }
  if (!goalRefsEqual(recordGoalRef, callerGoalRef)) {
    return isObservation(operation)
      ? { kind: "omit", code: "different_goal_instance" }
      : { kind: "reject", code: "record_instance_mismatch" };
  }
  if (requiresRoute(operation)) {
    if (routeGoalRef === null || !goalRefsEqual(routeGoalRef, callerGoalRef)) {
      return { kind: "reject", code: "route_instance_mismatch" };
    }
  }

  const current = goalRefsEqual(callerGoalRef, currentGoalRef);
  if (current) {
    if (
      (operation === "original_return_admit"
        || operation === "original_return_settle")
      && !facts.initialDeliveryProved
    ) {
      return { kind: "reject", code: "initial_delivery_unproved" };
    }
    return {
      kind: "allow",
      mode: "current_instance",
      goal_ref: wireGoalRef(callerGoalRef),
    };
  }
  if (!permitsHistorical(operation)) {
    return isObservation(operation)
      ? { kind: "omit", code: "different_goal_instance" }
      : { kind: "reject", code: "historical_mutation_forbidden" };
  }
  if (
    (operation === "original_return_admit"
      || operation === "original_return_settle")
    && !facts.initialDeliveryProved
  ) {
    return { kind: "reject", code: "initial_delivery_unproved" };
  }
  switch (operation) {
    case "result_publish":
    case "original_return_admit":
    case "original_return_settle":
      return {
        kind: "allow",
        mode: "historical_result",
        goal_ref: wireGoalRef(callerGoalRef),
      };
    case "history_inspect":
      return {
        kind: "allow",
        mode: "historical_read",
        goal_ref: wireGoalRef(callerGoalRef),
      };
    case "inbox_observe":
    case "read_record":
    case "receiver_decide":
    case "artifact_link":
    case "peer_return_observe":
    case "peer_return_consume":
      return { kind: "reject", code: "historical_mutation_forbidden" };
    default:
      return assertNever(operation, "unsupported collaboration operation");
  }
}

export function decideCollaborationLifecycle(
  value: unknown,
): CollaborationLifecycleDecision {
  const raw = jsonObject(value);
  if (!raw) {
    throw new EffectRuntimeRequestError(
      "collaboration lifecycle facts must be an object",
    );
  }
  const selectedOperation = operation(raw.operation);
  if (raw.profile_id === null) {
    if ((selectedOperation === "original_return_admit" || selectedOperation === "original_return_settle")
        && proveOriginalRequestDelivery(raw.initial_delivery).kind !== "proved") {
      return { kind: "reject", code: "initial_delivery_unproved" };
    }
    return { kind: "legacy" };
  }
  if (raw.profile_id !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const facts = lifecycleFacts(raw, selectedOperation);
  return "operation" in facts ? decideExactLifecycle(facts) : facts;
}
