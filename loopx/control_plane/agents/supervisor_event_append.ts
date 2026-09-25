/** Admission for one experimental supervisor log append under its file lock. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

function sequence(value: unknown, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new EffectRuntimeRequestError("supervisor sequence must be a safe integer");
  }
  return value;
}
function identity(value: unknown) {
  const row = requireJsonObject(value, "supervisor event identity");
  const fingerprint = requireNonEmptyString(row.fingerprint, "event fingerprint");
  if (!/^[a-f0-9]{64}$/.test(fingerprint)) throw new EffectRuntimeRequestError("invalid event fingerprint");
  return {event_id: requireNonEmptyString(row.event_id, "event_id"), fingerprint};
}
export function planSupervisorEventAppend(value: unknown): JsonObject {
  const request = requireJsonObject(value, "supervisor append plan");
  if (request.schema_version !== "loopx_supervisor_event_append_plan_v0") {
    throw new EffectRuntimeRequestError("invalid supervisor append schema");
  }
  const last = sequence(request.last_sequence, 0), event = identity(request.event);
  if (request.existing !== null) {
    const row = requireJsonObject(request.existing, "stored supervisor identity");
    const prior = identity(row), index = sequence(row.append_sequence, 1);
    if (index > last || prior.event_id !== event.event_id) {
      throw new EffectRuntimeRequestError("inconsistent stored supervisor identity");
    }
    return prior.fingerprint === event.fingerprint
      ? {status: "planned", kind: "replay", append_sequence: index}
      : {status: "rejected", reason_code: "event_id_conflict"};
  }
  return last === Number.MAX_SAFE_INTEGER
    ? {status: "rejected", reason_code: "event_sequence_exhausted"}
    : {status: "planned", kind: "append", append_sequence: last + 1};
}
