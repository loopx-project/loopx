/** Qualify an independent postcondition failure, never a Host's self-report.
 * Reuses the existing task-validation receipt and Turn failure/route vocabulary. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

export function taskValidationFailure(params: JsonObject): JsonObject {
  if (params.result_kind !== "validation_failed" || params.validation_stage === undefined)
    return {failure: null}; // Older receipts retain the existing generic repair route.
  if (params.validation_stage === "host_result_contract") return {failure: null};
  const receipt = requireJsonObject(params.receipt, "Turn validation receipt");
  const validation = requireJsonObject(params.validation, "independent task validation");
  const requireThat = (condition: boolean, message: string) => {
    if (!condition) throw new EffectRuntimeRequestError(message);
  };
  requireThat(params.validation_stage === "task_postcondition" && params.status === "failed"
    && receipt.ok === true && receipt.result_kind === "validation_failed" && receipt.status === "failed"
    && receipt.failed_phase === "validation"
    && JSON.stringify(receipt.completed_phases) === JSON.stringify(["host_execute", "typed_result"]),
  "task failure requires the original uncommitted validation boundary");
  requireThat(validation.schema_version === "loopx_turn_task_validation_v0" && validation.ok === false
    && ["failed", "inconclusive", "unavailable"].includes(String(validation.status))
    && ["repair_required", "replan_required"].includes(String(validation.recovery_kind))
    && Array.isArray(validation.errors), "qualified independent validation failure required");
  requireThat(typeof receipt.turn_key === "string" && receipt.turn_key.length > 0
    && typeof validation.validator_kind === "string" && validation.validator_kind.length > 0
    && validation.validator_kind.length <= 80 && typeof validation.summary === "string"
    && validation.summary.length > 0 && validation.summary.length <= 240,
  "bounded task failure identity and explanation required");
  return {failure: {stage: "task_postcondition", status: validation.status,
    recovery_kind: validation.recovery_kind, summary: validation.summary,
    validator_kind: validation.validator_kind, turn_key: receipt.turn_key,
    resume_mode: "revalidate_cached_result"}};
}
