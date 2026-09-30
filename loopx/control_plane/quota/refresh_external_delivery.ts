/** Per-settlement pause acknowledgement; never a provider permission grant. */
import { createHash } from "node:crypto";
import type { JsonObject, SettlementIdentity } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

export const EXTERNAL_DELIVERY_SCHEMA = "refresh_external_delivery_v0";
export const EXTERNAL_DELIVERY_EVENT = "refresh_external_delivery";
export interface ExternalDeliveryRequest {
  suppress: boolean;
  resume_key: string | null;
}

export function decodeExternalDelivery(value: unknown): ExternalDeliveryRequest | null {
  if (value == null) return null; // Existing non-delivery callers remain local.
  const input = requireJsonObject(value, "external_delivery");
  if (typeof input.suppress !== "boolean" ||
      (input.resume_key !== null &&
       (typeof input.resume_key !== "string" || !BARE_SHA256_PATTERN.test(input.resume_key))) ||
      (input.suppress && input.resume_key !== null)) {
    throw new EffectRuntimeRequestError("external_delivery requires suppress and a mutually exclusive resume key");
  }
  return { suppress: input.suppress, resume_key: input.resume_key };
}

function pauseKey(identity: SettlementIdentity, precedingEvent: JsonObject | null): string {
  return createHash("sha256").update(JSON.stringify([
    EXTERNAL_DELIVERY_SCHEMA, identity.effect_id, precedingEvent?.event_id ?? null,
  ])).digest("hex");
}

export function refreshExternalDelivery(
  request: ExternalDeliveryRequest | null,
  identity: SettlementIdentity,
  events: readonly JsonObject[],
  deliveryStage: boolean,
): JsonObject {
  let previous: JsonObject | null = null;
  let state: "paused" | "ready" | null = null;
  let key: string | null = null;
  let invalid = false;
  for (const event of events) {
    if (event.event_kind !== EXTERNAL_DELIVERY_EVENT || event.goal_id !== identity.goal_id ||
        event.agent_id !== identity.agent_id || event.run_id !== identity.turn_instance_id ||
        (event.todo_id ?? null) !== identity.todo_id) continue;
    const details = jsonObject(event.details);
    if ((details?.replan_obligation_id ?? null) !== identity.replan_obligation_id) continue;
    if (!details || details.schema_version !== EXTERNAL_DELIVERY_SCHEMA ||
        details.settlement_effect_id !== identity.effect_id || typeof event.event_id !== "string" ||
        (details.state !== "paused" && details.state !== "ready") ||
        typeof details.resume_key !== "string" || !BARE_SHA256_PATTERN.test(details.resume_key)) {
      invalid = true;
      break;
    }
    if (event.event_id === previous?.event_id) continue;
    if (details.state === "paused"
      ? state === "paused" || details.resume_key !== pauseKey(identity, previous)
      : state !== "paused" || details.resume_key !== key) {
      invalid = true;
      break;
    }
    state = details.state;
    key = details.resume_key;
    previous = event;
  }
  const result = (authorized: boolean, reason: string, error: string | null = null,
    transition: JsonObject | null = null): JsonObject => ({
    schema_version: EXTERNAL_DELIVERY_SCHEMA, authorized, reason,
    error_code: error, resume_key: key, transition,
  });
  // No send phase: do not reject a local closeout or consume a resume acknowledgement.
  if (!request || (!deliveryStage && !request.suppress)) return result(false, "local_recovery");
  if (invalid) return request.suppress ? result(false, "suppressed")
    : result(false, "invalid_pause_history", "external_delivery_history_invalid");
  const transition = (next: "paused" | "ready", resumeKey: string): JsonObject => ({
    schema_version: EXTERNAL_DELIVERY_SCHEMA, settlement_effect_id: identity.effect_id,
    replan_obligation_id: identity.replan_obligation_id, state: next, resume_key: resumeKey,
  });
  if (request.suppress) {
    if (state === "paused") return result(false, "suppressed");
    key = pauseKey(identity, previous);
    return result(false, "suppressed", null, transition("paused", key));
  }
  if (request.resume_key !== null) {
    if (key === null || request.resume_key !== key) {
      return result(false, "resume_key_mismatch", "external_delivery_resume_mismatch");
    }
    return result(true, "resume_acknowledged", null,
      state === "paused" ? transition("ready", key) : null);
  }
  if (state === "paused") {
    return result(false, "resume_confirmation_required", "external_delivery_resume_required");
  }
  return result(true, state === "ready" ? "resumed" : "legacy_per_call");
}
