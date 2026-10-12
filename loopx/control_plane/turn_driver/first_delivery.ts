/** Receipt-backed delivery observation; never settlement or direction authority. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject} from "../runtime_decode.ts";
import {originalTurnRequestsNoFollowup} from "../todos/completion_state.ts";

export function evaluateFirstDelivery(value: unknown): JsonObject {
  const request = requireJsonObject(value, "first delivery");
  if (request.phase === "project") {
    if (request.observation_unavailable === true) {
      return {schema_version: "first_delivery_progress_v0", stage: "observation_unavailable",
        goal_completion_certified: false,
        next_action: "Inspect unavailable checkpoint observations before resuming the affected Turn."};
    }
    const checkpoint = jsonObject(jsonObject(request.writeback_run)?.vision_checkpoint);
    const completion = jsonObject(request.completion_event);
    const resultCommitted = request.result_committed === true ||
      ["completed", "terminal_no_followup", "receipt_repaired"].includes(String(completion?.status));
    const outcomeRetained = checkpoint?.required === true && checkpoint.satisfied === false;
    const direction = jsonObject(request.direction_receipt);
    const notRequired = checkpoint?.decision === "not_required" && checkpoint.required === false && checkpoint.satisfied === true;
    const directionCommitted = checkpoint?.satisfied === true &&
      (jsonObject(checkpoint.read_context)?.purpose === "first_delivery" ||
        (request.checkpoint_continuation === true && checkpoint.read_context != null));
    const completionTodo = jsonObject(request.completion_todo);
    const identity = jsonObject(request.identity);
    const terminalRequired = completionTodo !== null && identity !== null &&
      originalTurnRequestsNoFollowup(completionTodo, identity);
    const settlementComplete = request.settlement_complete === true &&
      (!terminalRequired || request.terminal_settlement_complete === true);
    const unknown = request.unknown === true ||
      (jsonObject(request.direction_receipt)?.commit_attempt != null && !directionCommitted);
    const stage = unknown ? "operation_unknown"
      : !resultCommitted && !outcomeRetained && !directionCommitted && !notRequired ? "result_review_pending"
      : !directionCommitted && direction?.direction_stopped === true ? "direction_stopped"
      : !directionCommitted && direction?.direction_host_failure != null ? "direction_failed"
      : !directionCommitted && !notRequired ? "direction_pending"
      : settlementComplete ? "settled" : "settlement_pending";
    return {schema_version: "first_delivery_progress_v0", stage,
      result_committed: resultCommitted,
      outcome_retained: outcomeRetained || directionCommitted,
      direction_committed: directionCommitted,
      direction_required: !notRequired,
      quota_spent: request.quota_spent === true,
      goal_completion_certified: false,
      next_action: stage === "operation_unknown" ? "Read back the original Turn and its run artifacts; retain its identity."
        : stage === "result_review_pending" ? "Read the original Turn's result basis and validate the candidate before committing it."
        : stage === "direction_stopped" ? "Result retained; the current Agent stopped. Inspect the original Turn before explicitly resuming."
        : stage === "direction_failed" ? "Result retained; retry the original Turn's direction using the existing Host recovery policy."
        : stage === "direction_pending" ? "Result retained; review the current direction using the original Turn, then resume its remaining settlement."
        : stage === "settlement_pending" ? "Resume the original Turn's remaining settlement; retain successful writeback and quota receipts."
        : "Turn settlement is complete. Read the current Goal obligations before selecting further work."};
  }
  throw new TypeError("Delivery observation requires the project phase");
}
