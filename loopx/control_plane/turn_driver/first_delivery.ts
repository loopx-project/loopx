/** Receipt-backed delivery observation; never settlement or direction authority. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject} from "../runtime_decode.ts";

export function evaluateFirstDelivery(value: unknown): JsonObject {
  const request = requireJsonObject(value, "first delivery");
  if (request.phase === "project") {
    if (request.observation_unavailable === true) {
      return {schema_version: "first_delivery_progress_v0", stage: "observation_unavailable",
        goal_completion_certified: false,
        next_action: "Inspect unavailable checkpoint observations before resuming the affected Turn."};
    }
    const checkpoint = jsonObject(jsonObject(request.writeback_run)?.vision_checkpoint);
    const notRequired = checkpoint?.decision === "not_required" && checkpoint.required === false && checkpoint.satisfied === true;
    const directionCommitted = checkpoint?.satisfied === true &&
      jsonObject(checkpoint.read_context)?.purpose === "first_delivery";
    const unknown = request.unknown === true ||
      (jsonObject(request.direction_receipt)?.commit_attempt != null && !directionCommitted);
    const stage = unknown ? "operation_unknown"
      : request.result_committed !== true && !directionCommitted && !notRequired ? "result_review_pending"
      : !directionCommitted && !notRequired ? "direction_pending"
      : request.settlement_complete === true ? "settled" : "settlement_pending";
    return {schema_version: "first_delivery_progress_v0", stage,
      result_committed: request.result_committed === true,
      direction_committed: directionCommitted,
      direction_required: !notRequired,
      quota_spent: request.quota_spent === true,
      goal_completion_certified: false,
      next_action: stage === "operation_unknown" ? "Read back the original Turn and its run artifacts; retain its identity."
        : stage === "result_review_pending" ? "Read the original Turn's result basis and validate the candidate before committing it."
        : stage === "direction_pending" ? "Result retained; review the current direction using the original Turn, then resume its remaining settlement."
        : stage === "settlement_pending" ? "Resume the original Turn's remaining settlement; retain successful writeback and quota receipts."
        : "Turn settlement is complete. Read the current Goal obligations before selecting further work."};
  }
  throw new TypeError("Delivery observation requires the project phase");
}
