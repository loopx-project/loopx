import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyManagerReturnVerification,
  normalizeManagerReturnDeliveryAttempt,
} from "../../loopx/control_plane/collaboration/return_delivery.ts";

const attempt = {
  schema_version: "manager_return_delivery_attempt_v0",
  provider: "lark",
  message_ref: "om_provider_reply",
  intent_digest: `sha256:${"a".repeat(64)}`,
  provider_receipt: `sha256:${"b".repeat(64)}`,
};

test("normalizes the exact provider-neutral delivery attempt", () => {
  assert.deepEqual(normalizeManagerReturnDeliveryAttempt(attempt), attempt);
  // The provider accepted the write and reported no message id. The attempt is
  // still the record of that write, so the locator is typed as absent instead
  // of being rejected or faked.
  assert.deepEqual(
    normalizeManagerReturnDeliveryAttempt({ ...attempt, message_ref: null }),
    { ...attempt, message_ref: null },
  );
  assert.throws(
    () => normalizeManagerReturnDeliveryAttempt({ ...attempt, private_payload: "no" }),
    /unsupported or missing fields/,
  );
  assert.throws(
    () => normalizeManagerReturnDeliveryAttempt({ ...attempt, message_ref: "bad ref" }),
    /message_ref is invalid/,
  );
  assert.throws(
    () => normalizeManagerReturnDeliveryAttempt({ ...attempt, message_ref: "" }),
    /message_ref must be a non-empty string/,
  );
});

test("classifies verification without exposing provider prose", () => {
  assert.deepEqual(
    classifyManagerReturnVerification({
      verification_performed: true,
      reply_verified: true,
    }),
    {
      status: "delivered",
      error: null,
      verification: "reconciled_after_restart",
    },
  );
  assert.deepEqual(
    classifyManagerReturnVerification({
      verification_performed: false,
      reply_verified: false,
      blocker: "private provider outage detail",
    }),
    {
      status: "verification_required",
      error: "provider_verification_unavailable",
      verification: null,
    },
  );
  assert.deepEqual(
    classifyManagerReturnVerification({
      verification_performed: true,
      reply_verified: false,
      blocker: "private provider mismatch detail",
    }),
    {
      status: "explicit_unverified",
      error: "provider_delivery_mismatch",
      verification: null,
    },
  );
});

test("only typed resolution blockers stop an unavailable verification", () => {
  for (const blocker of [
    "return_authorization_unavailable",
    "original_route_unavailable",
    "initial_delivery_receipt_unavailable",
  ]) {
    assert.deepEqual(classifyManagerReturnVerification({
      verification_performed: false, reply_verified: false, blocker,
    }), { status: "explicit_unverified", error: blocker, verification: null });
  }
  for (const blocker of [null, "route lookup temporarily unavailable",
    "authorization service read timed out", "initial reply read interrupted",
    "original_route_unavailable: timeout", { reason: "original_route_unavailable" }]) {
    assert.deepEqual(classifyManagerReturnVerification({
      verification_performed: false, reply_verified: false, blocker,
    }), {
      status: "verification_required",
      error: "provider_verification_unavailable",
      verification: null,
    });
  }
});
