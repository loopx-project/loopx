/** Observe a refused selection from quota's decision; never decide admission. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

function code(value: unknown): string | null {
  return typeof value === "string" && /^[a-z][a-z0-9_]{0,159}$/.test(value) ? value : null;
}

export function readTurnSelectionRejection(value: unknown, requested: unknown): JsonObject {
  const row = requireJsonObject(value, "Turn selection refusal");
  if (row.schema_version !== "loopx_turn_selection_rejection_v0" || row.source !== "quota.should-run"
    || row.requested_todo_id !== requested || !["deferred","rejected","unavailable"].includes(String(row.state))
    || (row.reason_code !== null && code(row.reason_code) === null)
    || (row.recovery_action !== null && code(row.recovery_action) === null)
    || !Array.isArray(row.delivery_preemptions) || row.delivery_preemptions.length > 8
    || row.delivery_preemptions.some(value => code(value) === null)
    || ![true,false,null].includes(row.status_health_ok as boolean | null)
    || (row.contract_error_count !== null && (!Number.isSafeInteger(row.contract_error_count) || Number(row.contract_error_count) < 0)))
    throw new EffectRuntimeRequestError("matching bounded Turn selection refusal required");
  return Object.fromEntries(["schema_version","source","requested_todo_id","state","reason_code",
    "delivery_preemptions","recovery_action","status_health_ok","contract_error_count"].map(key => [key,row[key]]));
}

export function projectTurnSelectionRejection(params: JsonObject): JsonObject {
  const requested = params.requested_todo_id;
  if (typeof requested !== "string" || requested.length < 1 || requested.length > 256)
    throw new EffectRuntimeRequestError("bounded requested Todo identity required");
  const decision = requireJsonObject(params.decision, "current quota decision");
  const raw = decision.action_selection_qualification;
  const qualification = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as JsonObject : {};
  const state = qualification.requested_todo_id === requested
    && ["deferred", "rejected"].includes(String(qualification.state))
    ? qualification.state as string : "unavailable";
  const reasons = Array.isArray(qualification.delivery_preemptions)
    ? qualification.delivery_preemptions.filter(value => code(value) !== null).slice(0, 8) : [];
  const count = params.contract_error_count;
  return {
    error_code: `turn_todo_selection_${state}`,
    selection_rejection: {
      schema_version: "loopx_turn_selection_rejection_v0", source: "quota.should-run",
      requested_todo_id: requested, state,
      reason_code: state === "unavailable" ? null : code(qualification.reason),
      delivery_preemptions: state === "unavailable" ? [] : reasons,
      recovery_action: state === "unavailable" ? null : code(qualification.recovery_action),
      status_health_ok: typeof decision.status_health_ok === "boolean" ? decision.status_health_ok : null,
      contract_error_count: Number.isSafeInteger(count) && Number(count) >= 0 ? count : null,
    },
  };
}
