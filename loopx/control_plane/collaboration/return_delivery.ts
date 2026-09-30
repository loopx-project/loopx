import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

export const MANAGER_RETURN_DELIVERY_ATTEMPT_SCHEMA =
  "manager_return_delivery_attempt_v0";

const PROVIDER = /^[a-z][a-z0-9_-]{0,31}$/;
const OPAQUE_REF = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$/;
const DIGEST = ENVELOPED_SHA256_PATTERN;
const ATTEMPT_KEYS = [
  "schema_version",
  "provider",
  "message_ref",
  "intent_digest",
  "provider_receipt",
] as const;

function matchingString(value: unknown, label: string, pattern: RegExp): string {
  const result = requireNonEmptyString(value, label);
  if (!pattern.test(result)) {
    throw new EffectRuntimeRequestError(`${label} is invalid`);
  }
  return result;
}

/**
 * The provider locator for one recorded attempt, when the provider gave one.
 *
 * `null` is the typed state for "the provider accepted the write and reported
 * no message id". Such an attempt is still the durable record that a write
 * happened; what it cannot do is name a readback target. Keeping the key
 * required and the absence explicit is what stops a later retry from treating
 * an unlocatable write as a write that never happened.
 */
function optionalOpaqueRef(value: unknown, label: string): string | null {
  if (value === null) return null;
  return matchingString(value, label, OPAQUE_REF);
}

export function normalizeManagerReturnDeliveryAttempt(value: unknown): JsonObject {
  const attempt = requireJsonObject(value, "attempt");
  const keys = Object.keys(attempt).sort();
  if (keys.length !== ATTEMPT_KEYS.length
    || ATTEMPT_KEYS.some((key) => !keys.includes(key))) {
    throw new EffectRuntimeRequestError(
      "manager return delivery attempt has unsupported or missing fields",
    );
  }
  if (attempt.schema_version !== MANAGER_RETURN_DELIVERY_ATTEMPT_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "manager return delivery attempt schema is invalid",
    );
  }
  return {
    schema_version: MANAGER_RETURN_DELIVERY_ATTEMPT_SCHEMA,
    provider: matchingString(attempt.provider, "attempt.provider", PROVIDER),
    message_ref: optionalOpaqueRef(attempt.message_ref, "attempt.message_ref"),
    intent_digest: matchingString(
      attempt.intent_digest,
      "attempt.intent_digest",
      DIGEST,
    ),
    provider_receipt: matchingString(
      attempt.provider_receipt,
      "attempt.provider_receipt",
      DIGEST,
    ),
  };
}

export function classifyManagerReturnVerification(value: unknown): JsonObject {
  const outcome = requireJsonObject(value, "outcome");
  const performed = requireBoolean(
    outcome.verification_performed,
    "outcome.verification_performed",
  );
  const verified = requireBoolean(
    outcome.reply_verified,
    "outcome.reply_verified",
  );
  if (verified && !performed) {
    throw new EffectRuntimeRequestError(
      "a verified return requires a completed verification read",
    );
  }
  if (verified) {
    return {
      status: "delivered",
      error: null,
      verification: "reconciled_after_restart",
    };
  }
  if (!performed) {
    // A transport outage is not proof that the original route or grant was
    // revoked. Only exact, adapter-declared resolution reasons stop readback.
    const blocker = outcome.blocker;
    if (blocker === "return_authorization_unavailable"
      || blocker === "original_route_unavailable"
      || blocker === "initial_delivery_receipt_unavailable") {
      return { status: "explicit_unverified", error: blocker, verification: null };
    }
    return {
      status: "verification_required",
      error: "provider_verification_unavailable",
      verification: null,
    };
  }
  const blocker = typeof outcome.blocker === "string" ? outcome.blocker : "";
  const error = [
    "provider_delivery_intent_conflict",
    "provider_message_missing",
  ].includes(blocker)
    ? blocker
    : "provider_delivery_mismatch";
  return { status: "explicit_unverified", error, verification: null };
}
