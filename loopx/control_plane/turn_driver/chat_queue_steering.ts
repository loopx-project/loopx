import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";

// Queue handoff is part of the existing Turn/ingress owner. A reservation is
// persisted before native I/O; uncertain I/O must never become a queued start.
export const QUEUE_STEERING_SCHEMA = "loopx_chat_queue_steering_v1";
const NOT_DELIVERED = new Set([
  "attached_session_live_steering_unavailable", "live_steering_turn_mismatch",
  "live_steering_requires_active_turn", "live_steering_session_not_attached",
  "live_steering_turn_not_started",
]);

type QueueSteeringPlan =
  | {action: "dispatch" | "complete"; reservation: JsonObject}
  | {action: "release"; reservation: JsonObject; error_code: string}
  | {action: "reject" | "unresolved"; error_code: string};

function reservation(value: unknown): JsonObject | null {
  if (value === null || value === undefined) return null;
  const row = requireJsonObject(value, "queue steering reservation");
  if (row.schema_version !== QUEUE_STEERING_SCHEMA ||
      !["pending", "delivered", "failed"].includes(String(row.status))) {
    throw new Error("Invalid queue steering reservation");
  }
  requireNonEmptyString(row.client_ingress_id, "reservation.client_ingress_id");
  requireNonEmptyString(row.target_turn_id, "reservation.target_turn_id");
  requireNonEmptyString(row.operation_id, "reservation.operation_id");
  return row;
}

export function selectQueuedChatTurns(input: JsonObject): JsonObject {
  if (!Array.isArray(input.turns)) throw new Error("Queue turns must be an array");
  const ids = input.turns.flatMap(value => {
    const turn = requireJsonObject(value, "queue turn");
    const held = reservation(turn.reservation);
    if (input.capacity_at && held?.status !== "pending" && typeof turn.expires_at === "string" &&
        Date.parse(turn.expires_at) <= Date.parse(String(input.capacity_at))) return [];
    return turn.status === "queued" && turn.turn_id !== input.active_turn_id &&
      (input.include_reserved === true || held?.status !== "pending")
      ? [requireNonEmptyString(turn.turn_id, "turn.turn_id")] : [];
  });
  return {turn_ids: ids};
}

export function planQueuedChatSteering(input: JsonObject): QueueSteeringPlan {
  const session = requireJsonObject(input.session, "session");
  const turn = requireJsonObject(input.turn, "queued turn");
  const request = requireJsonObject(input.request, "request");
  const id = requireNonEmptyString(request.client_ingress_id, "client_ingress_id");
  const target = requireNonEmptyString(request.target_turn_id, "target_turn_id");
  const held = reservation(turn.reservation);
  const reject = (code: string): QueueSteeringPlan => ({action: "reject", error_code: code});
  if (session.status === "closed") return reject("session_closed");
  if (held?.status === "pending" &&
      (held.client_ingress_id !== id || held.target_turn_id !== target)) {
    return reject("queue_steering_in_progress");
  }
  const same = held?.client_ingress_id === id && held.target_turn_id === target;
  // Settlement/replay precedes current active-Turn checks: an acknowledged
  // native send remains acknowledged after that Turn finishes or changes.
  if (same && held.status === "delivered") return {action: "complete", reservation: held};
  const receipt = input.receipt == null ? null : requireJsonObject(input.receipt, "ingress receipt");
  if (receipt) {
    if (!same || receipt.expected_turn_id !== target || receipt.mode !== "live_steering" ||
        input.receipt_message_matches !== true) return reject("queue_steering_receipt_conflict");
    if (receipt.status === "delivered" && receipt.active_turn_id === target) {
      return {action: "complete", reservation: {...held, status: "delivered"}};
    }
    if (receipt.status === "failed" && NOT_DELIVERED.has(String(receipt.error_code))) {
      return {action: "release", error_code: String(receipt.error_code),
        reservation: {...held, status: "failed", error_code: receipt.error_code}};
    }
    return {action: "unresolved", error_code: "live_steering_delivery_unresolved"};
  }
  if (input.phase === "settle") {
    // No ingress was reserved: no provider call could have happened.
    return {action: "release", error_code: "live_steering_session_not_attached",
      reservation: {...held, status: "failed"}};
  }
  if (turn.status !== "queued" || session.active_turn_id === turn.turn_id) {
    return reject("queue_turn_already_started");
  }
  if (!["web", "external"].includes(String(turn.origin)) || turn.has_collaboration_request === true ||
      turn.has_external_agent_target === true || turn.loopx_execution === true) {
    return reject("queue_steering_source_unavailable");
  }
  if (session.active_turn_id !== target || !["starting", "running"].includes(String(input.active_status))) {
    if (same && held.status === "pending") return {action: "release",
      error_code: "live_steering_turn_mismatch", reservation: {...held, status: "failed"}};
    return reject("live_steering_turn_mismatch");
  }
  // Native live steering currently accepts text only. Retain attachments and
  // the pending Turn instead of silently dropping them during conversion.
  if (turn.attachment_count !== 0) return reject("queue_steering_attachments_unavailable");
  return {action: "dispatch", reservation: {
    schema_version: QUEUE_STEERING_SCHEMA, client_ingress_id: id,
    target_turn_id: target, operation_id: requireNonEmptyString(request.operation_id, "operation_id"), status: "pending",
  }};
}
