/** Report admission is not permission to launch a child or spend quota. */
import type {JsonObject} from "../effect_program.ts";
import type {ReceiptBoundReplayPhase} from "../quota/settlement_phase.ts";

export const NATIVE_CHILD_REPORT_ADMISSION_SCHEMA = "native_child_report_admission_v0";

export interface NativeChildReportAdmission extends JsonObject {
  schema_version: typeof NATIVE_CHILD_REPORT_ADMISSION_SCHEMA;
  report_permission: "new_operation" | "existing_operation_only" | "not_admitted";
  reason_code: "turn_work_admitted" | "turn_closeout_started" | "guard_work_not_admitted";
  settlement_effect_id: string;
  turn_phase: ReceiptBoundReplayPhase;
}

/** Consume committed admission facts, not a whitelist of current status labels.
 * Identity and phase must first be verified by the settlement readback owner. */
export function nativeChildReportAdmission(
  guardDetails: JsonObject,
  guardStatus: unknown,
  effectId: string,
  phase: ReceiptBoundReplayPhase,
  closeoutStarted: boolean,
): NativeChildReportAdmission {
  const projectionAbsent = ["must_attempt_work", "delivery_allowed", "quiet_noop_allowed"]
    .every((field) => !Object.hasOwn(guardDetails, field));
  // Old ordinary/managed guards predate the work projection. Never use this
  // compatibility branch to turn partial or negative modern facts into work.
  const legacyAdmission = projectionAbsent &&
    (guardStatus === "normal_run" || guardStatus === "turn_run_once") &&
    (guardDetails.ok === undefined || guardDetails.ok === true) &&
    (guardDetails.should_run === undefined || guardDetails.should_run === true);
  const admitted = legacyAdmission || (
    guardDetails.ok === true && guardDetails.should_run === true &&
    guardDetails.must_attempt_work === true && guardDetails.delivery_allowed === true &&
    guardDetails.quiet_noop_allowed === false
  );
  const open = phase === "open" && !closeoutStarted;
  return {
    schema_version: NATIVE_CHILD_REPORT_ADMISSION_SCHEMA,
    report_permission: !admitted ? "not_admitted"
      : open ? "new_operation" : "existing_operation_only",
    reason_code: !admitted ? "guard_work_not_admitted"
      : open ? "turn_work_admitted" : "turn_closeout_started",
    settlement_effect_id: effectId,
    turn_phase: phase,
  };
}
