/** Direction-only Host response. The original result remains immutable. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {normalizeVisionUnchangedReason} from "../goals/vision_checkpoint.ts";

export function evaluateFirstDelivery(value: unknown): JsonObject {
  const request = requireJsonObject(value, "first delivery");
  if (request.phase === "project") {
    const stage = request.unknown === true ? "operation_unknown"
      : request.direction_committed !== true ? "direction_pending"
      : request.settlement_complete === true ? "settled" : "settlement_pending";
    return {schema_version: "first_delivery_progress_v0", stage,
      result_committed: request.result_committed === true,
      direction_committed: request.direction_committed === true,
      quota_spent: request.quota_spent === true,
      goal_completion_certified: false,
      next_action: stage === "operation_unknown" ? "Read back the original Turn and its run artifacts; retain its identity."
        : stage === "direction_pending" ? "Resume the original Turn or read first_delivery context and judge the current direction. Retain the committed result."
        : stage === "settlement_pending" ? "Resume the original Turn's remaining settlement; retain successful writeback and quota receipts."
        : "Turn settlement is complete. Read the current Goal obligations before selecting further work."};
  }
  const response = requireJsonObject(request.response, "direction response");
  if (Object.keys(response).some(key => !["read_context_id", "decision", "agent_vision", "agent_vision_json", "vision_unchanged_reason"].includes(key)) ||
      response.read_context_id !== requireNonEmptyString(request.read_context_id, "read context id")) {
    throw new EffectRuntimeRequestError("Direction response must echo the delivered read identity and contain only direction fields.");
  }
  if (!["continue", "revalidate_result", "terminal_ready"].includes(String(response.decision))) {
    throw new EffectRuntimeRequestError("Direction decision must be continue, revalidate_result or terminal_ready.");
  }
  if (response.agent_vision != null && response.agent_vision_json) throw new EffectRuntimeRequestError("Duplicate direction Vision payload.");
  const rawVision = response.agent_vision ?? (response.agent_vision_json ? JSON.parse(String(response.agent_vision_json)) : null);
  const vision = rawVision == null ? null : requireJsonObject(rawVision, "agent vision");
  const reason = normalizeVisionUnchangedReason(response.vision_unchanged_reason);
  if ((vision === null) === (reason === null)) {
    throw new EffectRuntimeRequestError("Direction response requires exactly one authored Vision or unchanged reason.");
  }
  return {read_context_id: response.read_context_id, decision: response.decision,
    agent_vision: vision, vision_unchanged_reason: reason};
}
