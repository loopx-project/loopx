/** Shared typed semantic receipt guard; no settlement or replan decision imports. */
import type { JsonObject } from "../effect_program.ts";
import { createHash } from "node:crypto";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireNonEmptyString } from "../runtime_decode.ts";

const REPLAN_OBLIGATION_ID_PATTERN = /^replan-[a-f0-9]{16}$/;
export const SEMANTIC_REPLAN_GUARD_SCHEMA = "semantic_replan_guard_v0";

export const AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID = "autonomous_replan_default_v0";
export const AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION =
  "stop only the affected path when required access, destructive git, production actions, " +
  "or owner-only decisions exceed existing user authority. Keep authorized private reads " +
  "within their storage and publication boundaries; do not expose credentials or disclose " +
  "private material beyond that authority. Continue independent authorized replan work " +
  "while honoring explicit stops";
export const AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION =
  "stop this replan path until a compatible reader validates its digest-bound review guard";

export function normalizeHeartbeatReplanObligationId(
  value: unknown,
): string | null {
  const candidate = String(value ?? "").trim();
  return candidate && REPLAN_OBLIGATION_ID_PATTERN.test(candidate)
    ? candidate
    : null;
}

const normalizeReplanObligationId = normalizeHeartbeatReplanObligationId;

export function projectSemanticReplanGuard(
  receiptDetails: JsonObject,
): JsonObject {
  const hasCapability = ["semantic_replan_capability_id", "semantic_replan_gap_id", "semantic_replan_frontier_revision"]
    .some(field => Object.hasOwn(receiptDetails, field));
  const hasReview = ["semantic_replan_review_kind", "semantic_replan_review_stop_condition",
    "semantic_replan_review_stop_condition_sha256", "semantic_replan_review_stop_contract_id"]
    .some(field => Object.hasOwn(receiptDetails, field));
  if (hasCapability && hasReview) throw new EffectRuntimeRequestError(
    "a review source cannot replace a capability guard", "malformed_settlement_state");
  if (!Object.hasOwn(receiptDetails, "semantic_replan_obligation_id")) {
    if (hasCapability) throw new EffectRuntimeRequestError("a capability guard requires a selected obligation", "malformed_settlement_state");
    if (hasReview) throw new EffectRuntimeRequestError("a review source requires a selected obligation", "malformed_settlement_state");
    return {
      schema_version: SEMANTIC_REPLAN_GUARD_SCHEMA,
      scope: "legacy_unscoped",
      selected_obligation_id: null,
    };
  }
  const rawObligationId = receiptDetails.semantic_replan_obligation_id;
  if (rawObligationId === "" || rawObligationId === null) {
    if (hasCapability) throw new EffectRuntimeRequestError("a capability guard requires a selected obligation", "malformed_settlement_state");
    if (hasReview) throw new EffectRuntimeRequestError("a review source requires a selected obligation", "malformed_settlement_state");
    return {
      schema_version: SEMANTIC_REPLAN_GUARD_SCHEMA,
      scope: "turn_guard",
      selected_obligation_id: null,
    };
  }
  const selectedObligationId = normalizeReplanObligationId(rawObligationId);
  if (selectedObligationId === null) {
    throw new EffectRuntimeRequestError(
      "heartbeat receipt semantic replan guard is malformed",
      "malformed_settlement_state",
    );
  }
  const reviewKind = hasReview ? requireNonEmptyString(receiptDetails.semantic_replan_review_kind, "receipt review kind") : null;
  const stop = Object.hasOwn(receiptDetails, "semantic_replan_review_stop_condition")
    ? requireNonEmptyString(receiptDetails.semantic_replan_review_stop_condition, "receipt review stop condition") : null;
  const stopDigest = Object.hasOwn(receiptDetails, "semantic_replan_review_stop_condition_sha256")
    ? requireNonEmptyString(receiptDetails.semantic_replan_review_stop_condition_sha256,
      "receipt review stop condition digest") : null;
  const stopContractId = Object.hasOwn(receiptDetails, "semantic_replan_review_stop_contract_id")
    ? requireNonEmptyString(receiptDetails.semantic_replan_review_stop_contract_id,
      "receipt review stop contract id") : null;
  if (hasReview && stop === null && stopDigest === null) throw new EffectRuntimeRequestError(
    "a receipt review requires a stop condition or digest", "malformed_settlement_state");
  if (stop && stop.length > 240) throw new EffectRuntimeRequestError(
    "receipt review stop condition exceeds scalar metadata bounds", "malformed_settlement_state");
  if (stopDigest !== null && !BARE_SHA256_PATTERN.test(stopDigest)) throw new EffectRuntimeRequestError(
    "receipt review stop condition digest is malformed", "malformed_settlement_state");
  if (stopContractId !== null &&
      (stopContractId !== AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID || stopDigest === null ||
       createHash("sha256").update(AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION, "utf8").digest("hex") !== stopDigest)) {
    throw new EffectRuntimeRequestError(
      "receipt review stop contract does not match its digest", "malformed_settlement_state");
  }
  if (stop !== null && stopDigest !== null &&
      stop !== AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION &&
      createHash("sha256").update(stop, "utf8").digest("hex") !== stopDigest) {
    throw new EffectRuntimeRequestError(
      "receipt review stop condition digest does not match its value", "malformed_settlement_state");
  }
  return {
    schema_version: SEMANTIC_REPLAN_GUARD_SCHEMA,
    scope: "turn_guard",
    selected_obligation_id: selectedObligationId,
    ...(hasReview ? {selected_review_source: {kind: reviewKind,
      ...(stop === null ? {} : {stop_condition: stop}),
      ...(stopDigest === null ? {} : {stop_condition_sha256: stopDigest}),
      ...(stopContractId === null ? {} : {stop_contract_id: stopContractId})}} : {}),
    ...(hasCapability ? {
      selected_capability_guard: decodeCapabilityGuard({schema_version: "semantic_replan_capability_guard_v0",
        capability_id: receiptDetails.semantic_replan_capability_id, gap_id: receiptDetails.semantic_replan_gap_id,
        frontier_revision: receiptDetails.semantic_replan_frontier_revision}),
    } : {}),
  };
}

function decodeCapabilityGuard(value: unknown): JsonObject {
  const guard = jsonObject(value);
  if (!guard || guard.schema_version !== "semantic_replan_capability_guard_v0"
    || typeof guard.capability_id !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(guard.capability_id)
    || typeof guard.gap_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(guard.gap_id)
    || typeof guard.frontier_revision !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(guard.frontier_revision)
    || Object.keys(guard).some(key => !["schema_version", "capability_id", "gap_id", "frontier_revision"].includes(key))) {
    throw new EffectRuntimeRequestError("heartbeat receipt capability guard is malformed", "malformed_settlement_state");
  }
  return guard;
}
