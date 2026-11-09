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

function mutation(value: unknown): JsonObject | null {
  if (value == null) return null;
  const row = requireJsonObject(value, "queue input receipt");
  requireNonEmptyString(row.operation_id, "queue input operation_id");
  if ((row.action !== "edit" && row.action !== "cancel") ||
      !Number.isSafeInteger(row.expected_revision) || Number(row.expected_revision) < 0 ||
      row.input_revision !== Number(row.expected_revision) + 1 ||
      row.action === "edit" && typeof row.message !== "string") throw new Error("Invalid queue input receipt");
  return row;
}

export function selectQueuedChatTurns(input: JsonObject): JsonObject {
  if (!Array.isArray(input.turns)) throw new Error("Queue turns must be an array");
  const ids = input.turns.flatMap(value => {
    const turn = requireJsonObject(value, "queue turn");
    const held = reservation(turn.reservation);
    const editing = mutation(turn.mutation);
    if (input.capacity_at && held?.status !== "pending" && !editing && typeof turn.expires_at === "string" &&
        Date.parse(turn.expires_at) <= Date.parse(String(input.capacity_at))) return [];
    return turn.status === "queued" && turn.turn_id !== input.active_turn_id &&
      (input.include_reserved === true || (held?.status !== "pending" && !editing))
      ? [requireNonEmptyString(turn.turn_id, "turn.turn_id")] : [];
  });
  return {turn_ids: ids};
}

/** Select delivery for a provider-verified native mention in an exact Session.
 * A recorded ingress owns retries, even after the observed Turn has changed.
 */
export function planNativeMentionIngress(input: JsonObject): JsonObject {
  if (input.mentioned !== true) return {action: "configured"};
  if (input.binding_matches !== true) return {action: "reject", error_code: "live_steering_session_not_attached"};
  if (input.text_only !== true) return {action: "reject", error_code: "queue_steering_attachments_unavailable"};

  if (input.receipt != null) {
    const receipt = requireJsonObject(input.receipt, "mention ingress receipt");
    if (input.receipt_message_matches !== true) return {action: "unresolved"};
    if (receipt.mode === "session_queue") return {action: "enqueue"};
    if (receipt.mode !== "live_steering") return {action: "unresolved"};
    if (receipt.status === "delivered" && typeof receipt.active_turn_id === "string" &&
        receipt.active_turn_id && (receipt.expected_turn_id == null || receipt.expected_turn_id === receipt.active_turn_id)) {
      return {action: "delivered", target_turn_id: receipt.active_turn_id};
    }
    if (receipt.status === "failed" && NOT_DELIVERED.has(String(receipt.error_code))) {
      return {action: "reject", error_code: receipt.error_code};
    }
    return {action: "unresolved"};
  }

  if (input.queued_turn_exists === true) return {action: "enqueue"};
  // A recovered source event without a Chat receipt cannot bind to a newer
  // active Turn. Keep the original message available for explicit recovery.
  if (input.may_deliver_new !== true) return {action: "unresolved"};
  if (input.target_turn_id === null) return {action: "enqueue"};
  return {action: "steer", target_turn_id: requireNonEmptyString(input.target_turn_id, "target_turn_id")};
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
  if (turn.mutation != null) return reject("queue_mutation_in_progress");
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

/** Positive native evidence can settle an uncertain send, never authorize a retry. */
export function planChatSteeringReadback(input: JsonObject): JsonObject {
  const receipt = requireJsonObject(input.receipt, "steering receipt");
  const capture = receipt.native_steering;
  if (capture == null) return {action: "unresolved"};
  const native = requireJsonObject(capture, "native steering capture");
  const turn = requireJsonObject(input.turn, "original steering Turn");
  const validIds = [native.local_turn_id, native.thread_id, native.turn_id, native.client_message_id]
    .every(value => typeof value === "string" && value.length > 0);
  if (!validIds || receipt.mode !== "live_steering" ||
      !["pending", "failed"].includes(String(receipt.status)) ||
      typeof receipt.message !== "string" || !receipt.message.trim() ||
      turn.turn_id !== native.local_turn_id || turn.upstream_turn_id !== native.turn_id ||
      (receipt.expected_turn_id != null && receipt.expected_turn_id !== native.local_turn_id) ||
      input.session_thread_id !== native.thread_id || input.adapter_thread_id !== native.thread_id) {
    return {action: "unresolved"};
  }
  if (input.items == null) return {action: "read", turn_id: native.turn_id};
  if (input.inventory_complete !== true || !Array.isArray(input.items)) return {action: "unresolved"};
  const items = input.items.map(value => requireJsonObject(value, "native Turn entry"));
  if (items.some(entry => entry.turnId !== native.turn_id)) return {action: "unresolved"};
  const matches = items.map(entry => requireJsonObject(entry.item, "native Turn item"))
    .filter(item => item.type === "userMessage" && item.clientId === native.client_message_id);
  if (matches.length !== 1 || !Array.isArray(matches[0].content) || matches[0].content.length !== 1) {
    return {action: "unresolved"};
  }
  const text = requireJsonObject(matches[0].content[0], "native user text");
  return {action: text.type === "text" && text.text === receipt.message.trim() ? "confirmed" : "unresolved"};
}

/** Pending input changes share the queue consumer's Session/Turn fence.
 * The pending receipt excludes dispatch until transcript/event IO is finished.
 * Retried operation identities describe the same request, even after a claim.
 */
export function planQueuedChatMutation(input: JsonObject): JsonObject {
  const session = requireJsonObject(input.session, "session");
  const turn = requireJsonObject(input.turn, "queued turn");
  const request = requireJsonObject(input.request, "queue input request");
  const operationId = requireNonEmptyString(request.operation_id, "operation_id");
  if (operationId.length > 160 || !/^[A-Za-z0-9._-]+$/.test(operationId) ||
      !Number.isSafeInteger(request.expected_revision) || Number(request.expected_revision) < 0 ||
      (request.action !== "edit" && request.action !== "cancel") ||
      Object.keys(request).some(key => !["operation_id", "expected_revision", "action", "message"].includes(key))) {
    throw new Error("Invalid queue input request");
  }
  if (request.action === "edit" && (typeof request.message !== "string" ||
      !request.message.trim() || [...request.message].length > 12000) ||
      request.action === "cancel" && "message" in request) throw new Error("Invalid queued message");
  const reject = (error_code: string): JsonObject => ({action: "reject", error_code});
  if (session.status === "closed") return reject("session_closed");
  if (turn.origin !== "web" || turn.has_collaboration_request === true ||
      turn.has_external_agent_target === true || turn.loopx_execution === true || turn.has_acceptance === true) {
    return reject("queue_mutation_source_unavailable");
  }
  const pending = mutation(turn.mutation);
  const operations = turn.operations ?? [];
  if (!Array.isArray(operations)) throw new Error("Invalid queue input receipts");
  const previous = operations.map(row => mutation(row)!)
    .find(row => row.operation_id === operationId);
  const held = pending ?? previous;
  if (held) {
    if (held.operation_id !== operationId) return reject("queue_mutation_in_progress");
    if (held.action !== request.action || held.expected_revision !== request.expected_revision ||
        held.message !== request.message) return reject("queue_mutation_identity_conflict");
    if (!pending) return {action: "replay", receipt: held};
  }
  if (turn.status !== "queued" || session.active_turn_id === turn.turn_id) return reject("queue_turn_already_started");
  if (reservation(turn.reservation)?.status === "pending") return reject("queue_steering_in_progress");
  if (request.expected_revision !== (turn.input_revision ?? 0)) return reject("queue_input_revision_conflict");
  const receipt = held ?? {...request, input_revision: Number(request.expected_revision) + 1};
  return {action: "apply", receipt,
    message_patch: request.action === "edit" ? {text: request.message, queue_input_revision: receipt.input_revision}
      : {queue_input_cancelled: true, queue_input_revision: receipt.input_revision},
    turn_patch: request.action === "edit" ? {message: request.message, input_revision: receipt.input_revision}
      : {status: "interrupted", input_revision: receipt.input_revision},
    event: request.action === "edit" ? {kind: "turn.input_edited", payload: {
      operation_id: operationId, input_revision: receipt.input_revision}}
      : {kind: "turn.interrupted", payload: {reason: "queued_input_cancelled", operation_id: operationId}},
  };
}
