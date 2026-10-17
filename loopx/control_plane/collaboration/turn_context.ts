/** A private per-Turn agenda over existing receipts, never a second work lifecycle. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

function item(value: unknown): JsonObject | null {
  const row = requireJsonObject(value, "delegation observation");
  const direction = requireStringLiteral(row.direction, ["incoming", "outgoing"], "delegation direction");
  const request = requireJsonObject(row.request, "delegation request");
  if (typeof request.request_id !== "string" || !BARE_SHA256_PATTERN.test(request.request_id)) {
    throw new EffectRuntimeRequestError("invalid delegation request identity");
  }
  const receipt = requireJsonObject(row.receipt, "delegation receipt");
  const kind = requireStringLiteral(receipt.kind,
    ["pending", "awaiting_conclusion", "settled", "receipt_unavailable"], "delegation receipt state");
  const control = receipt.request_control_view as JsonObject | null | undefined;
  const cancelled = control?.control_state === "cancelled";
  // Cancellation of a request does not release an in-flight publication reservation.
  if (cancelled && control?.active_reservation == null) return null;
  if (!Array.isArray(row.results)) throw new EffectRuntimeRequestError("delegation results are required");
  const results = row.results.map(value => requireJsonObject(value, "delegation result"))
    .filter(result => result.request_revision === (control?.revision ?? 1));
  const incomplete = results.filter(result => result.status !== "delivered" && result.status !== "superseded");
  const unconsumed = results.filter(result => result.consumed !== true);
  let step: string;
  if (kind === "receipt_unavailable" || row.evidence_unavailable === true) step = "recover_evidence";
  else if (cancelled) step = "reconcile_reservation";
  else if (kind !== "settled") step = direction === "outgoing" ? "await_peer_result"
    : receipt.recorded_decision == null ? "assess_request"
    : receipt.recorded_decision === "adopt" ? "review_request_work" : "return_answer";
  else if (direction === "outgoing" && unconsumed.length) step = "consume_peer_result";
  else if (incomplete.length) step = "await_return_delivery";
  else if (request.source_kind === "peer" && unconsumed.length) step = "await_peer_consumption";
  else return null;
  const purpose = String(control?.brief && (control.brief as JsonObject).purpose
    || request.purpose || "").replace(/\s+/g, " ").trim();
  const todos = Array.isArray(row.todo_ids) ? row.todo_ids : [];
  return {
    request_id: request.request_id, direction,
    ...(request.goal_ref ? {goal_ref: request.goal_ref} : {}),
    request_revision: control?.revision ?? 1,
    ...(control ? {request_digest: control.revision_digest} : {}),
    ...(request.source_agent_id ? {source_agent_id: request.source_agent_id} : {}),
    agent_id: request.agent_id,
    purpose: [...purpose].slice(0, 120).join(""), purpose_truncated: [...purpose].length > 120,
    receipt_state: kind, decision: receipt.recorded_decision ?? null, step,
    ...(todos.length ? {todo_ids: todos.slice(0, 4), additional_todo_count: Math.max(0, todos.length - 4)} : {}),
    ...(results.length ? {pending_return_count: incomplete.length, unconsumed_return_count: unconsumed.length,
      returns: results.slice(-3).map(result => ({result_key: result.result_key,
        status: result.status, consumed: result.consumed})), additional_return_count: Math.max(0, results.length - 3)} : {}),
  };
}

function visibleRows(rows: JsonObject[]): JsonObject[] {
  const incoming = rows.filter(row => row.direction === "incoming");
  const outgoing = rows.filter(row => row.direction === "outgoing");
  const visible: JsonObject[] = [];
  for (let index = 0; visible.length < 20 && index < Math.max(incoming.length, outgoing.length); index++) {
    if (incoming[index]) visible.push(incoming[index]);
    if (outgoing[index] && visible.length < 20) visible.push(outgoing[index]);
  }
  while (visible.length && Buffer.byteLength(JSON.stringify(visible), "utf8") > 10_000) visible.pop();
  return visible;
}

/** Input is already scope-checked by the existing inbox and result owners. */
export function projectDelegationTurnItems(params: JsonObject): JsonObject {
  if (!Array.isArray(params.observations) || params.observations.length > 128) {
    throw new EffectRuntimeRequestError("delegation observation batch must contain at most 128 requests");
  }
  const rows = params.observations.map(item).filter((value): value is JsonObject => value !== null);
  return {items: visibleRows(rows), incoming_count: rows.filter(row => row.direction === "incoming").length,
    outgoing_count: rows.filter(row => row.direction === "outgoing").length};
}

export function projectDelegationTurnContext(params: JsonObject): JsonObject {
  if (!Array.isArray(params.batches) || params.batches.length > 64) {
    throw new EffectRuntimeRequestError("delegation context must contain at most 64 batches");
  }
  const batches = params.batches.map(value => requireJsonObject(value, "delegation turn batch"));
  const rows = batches.flatMap(batch => {
    if (!Array.isArray(batch.items)) throw new EffectRuntimeRequestError("delegation batch items are required");
    return batch.items.map(value => requireJsonObject(value, "delegation turn item"));
  });
  const total = (key: string) => batches.reduce((sum, batch) => {
    const count = batch[key];
    if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) {
      throw new EffectRuntimeRequestError("invalid delegation batch count");
    }
    return sum + count;
  }, 0);
  const incoming = total("incoming_count"), outgoing = total("outgoing_count");
  const visible = visibleRows(rows);
  return {ok: true, read_complete: false, unfinished_delegations: {
    count_unit: "request", items: visible, incoming_count: incoming, outgoing_count: outgoing,
    omitted_count: incoming + outgoing - visible.length,
    scan_complete: params.scan_complete === true && params.unavailable_count === 0,
    unavailable_count: params.unavailable_count, observed_at: params.observed_at,
    read_command: params.read_command,
    instruction: "Reconcile unfinished incoming and sent requests every Turn, including already-read or adopted work. " +
      "Counts describe requests, not distinct tasks; multiple requests may link to the same Todo. " +
      "These are receipt facts, not verified task completion or a priority/authority grant. " +
      "Read the full current request before assessing, executing or returning it; use the existing Inbox read/status and result consumption routes. " +
      "A linked Todo being done does not return an answer; a queued conclusion is not delivered; delivery is not requester adoption. " +
      "Omitted items or incomplete coverage are not an empty queue. Recover unavailable receipts without replaying work or effects.",
  }};
}
