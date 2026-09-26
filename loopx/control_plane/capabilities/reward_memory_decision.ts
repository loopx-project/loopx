import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireStringLiteral } from "../runtime_decode.ts";

const MODES = ["execute", "preview", "recall_only"] as const;
const KINDS = ["context_delivery", "semantic_application"] as const;
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$/;

function token(value: unknown, name: string, optional = false): string | null {
  if (optional && (value === null || value === undefined)) return null;
  if (typeof value !== "string" || !TOKEN.test(value)) {
    throw new EffectRuntimeRequestError(`${name} must be a compact reference`);
  }
  return value;
}

function boolean(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") throw new EffectRuntimeRequestError(`${name} must be boolean`);
  return value;
}

function count(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new EffectRuntimeRequestError(`${name} must be a nonnegative integer`);
  }
  return value as number;
}

/** Query-ready consumption policy; no config, provider content or model calls. */
export function planRewardMemoryDecision(params: JsonObject): JsonObject {
  const mode = requireStringLiteral(params.mode, MODES, "mode");
  const kind = params.application_kind === null || params.application_kind === undefined
    ? null : requireStringLiteral(params.application_kind, KINDS, "application_kind");
  const ready = boolean(params.query_ready, "query_ready");
  const hasApplier = boolean(params.has_applier, "has_applier");
  const packet: JsonObject = {
    schema_version: "reward_memory_decision_consumption_v0",
    mode, application_kind: kind,
    application_id: token(params.application_id, "application_id"),
    artifact_ref: token(params.artifact_ref, "artifact_ref", true),
    surface_id: token(params.surface_id, "surface_id"),
    status: "incomplete", reason_code: null, should_recall: false,
    decision_consumption_complete: false, context_delivery_verified: false,
    semantic_disposition: null, result_readback_verified: false,
    provider_call_count: 0, filtered_count: 0, preserve_base_output: true,
    research_may_continue: true, grants_new_action_authority: false,
    external_writes_performed: false, raw_content_captured: false,
    utility_verified: false,
  };
  if (!ready) return {...packet, reason_code: "query_not_ready"};
  if (mode === "preview") return {...packet, status: "preview"};
  if (mode === "execute") {
    if (!kind || !hasApplier) return {...packet, reason_code: "application_strategy_required"};
    if (!packet.artifact_ref) return {...packet, reason_code: "current_artifact_binding_required"};
  }
  return {...packet, status: "ready", should_recall: true};
}

/** Reduce original recall/application receipts, never interpret private lessons. */
export function projectRewardMemoryDecision(params: JsonObject): JsonObject {
  const plan = planRewardMemoryDecision(requireJsonObject(params.request, "request"));
  if (!plan.should_recall) return plan;
  const observation = requireJsonObject(params.observation, "observation");
  const hookStatus = requireStringLiteral(observation.status, [
    "disabled", "guard_rejected", "provider_unavailable", "not_available",
    "available_not_applied", "failed", "applied", "ignored", "refuted",
  ] as const, "observation.status");
  const readback = boolean(observation.result_readback_verified, "result_readback_verified");
  const recallStatus = observation.recall_status === null ? null
    : requireStringLiteral(observation.recall_status, ["completed", "empty", "provider_unavailable", "guard_blocked"] as const, "recall_status");
  const packet: JsonObject = {
    ...plan, should_recall: false,
    boundary_reason_code: observation.boundary_reason_code == null ? null
      : requireStringLiteral(observation.boundary_reason_code, [
        "automation_config_invalid", "surface_profile_or_query_invalid",
        "exact_corpus_request_invalid", "surface_has_no_recall_corpus",
      ] as const, "boundary_reason_code"),
    provider_call_count: count(observation.provider_call_count, "provider_call_count"),
    filtered_count: count(observation.filtered_count, "filtered_count"),
    result_readback_verified: readback,
    recall_status: recallStatus,
  };
  if (hookStatus === "provider_unavailable") {
    return {...packet, status: "provider_unavailable", reason_code: "provider_unavailable"};
  }
  if (hookStatus === "guard_rejected" || hookStatus === "disabled" || recallStatus === "guard_blocked") {
    return {...packet, status: "incomplete", reason_code: "recall_boundary_rejected"};
  }
  if (!readback) return {...packet, status: "empty", reason_code: packet.filtered_count
    ? "all_provider_items_filtered" : "provider_returned_no_items"};
  if (plan.mode === "recall_only") return {...packet, status: "recalled"};
  const receipt = requireJsonObject(observation.application_receipt, "application_receipt");
  const digests = receipt.memory_ref_digests;
  const attributed = Array.isArray(digests) && digests.length > 0 && digests.length <= 8 &&
    digests.every((item) => typeof item === "string" && /^[0-9a-f]{16}$/.test(item));
  const bound = receipt.schema_version === "reward_memory_application_receipt_v0" &&
    receipt.application_id === plan.application_id && receipt.artifact_ref === plan.artifact_ref &&
    receipt.surface_id === plan.surface_id && receipt.outcome === hookStatus &&
    receipt.current_artifact_verified === true && receipt.result_readback_verified === true && attributed;
  if (!bound || hookStatus === "failed" || hookStatus === "available_not_applied") {
    return {...packet, status: "incomplete", reason_code: "application_evidence_incomplete"};
  }
  // A delivered context is available for reasoning; it is not the reasoning disposition.
  if (plan.application_kind === "context_delivery") {
    return hookStatus === "applied"
      ? {...packet, status: "context_delivered", memory_ref_digests: digests,
        context_delivery_verified: true, preserve_base_output: false}
      : {...packet, status: "incomplete", reason_code: "context_delivery_not_verified"};
  }
  if (hookStatus !== "applied" && hookStatus !== "ignored" && hookStatus !== "refuted") {
    return {...packet, status: "incomplete", reason_code: "semantic_disposition_required"};
  }
  return {
    ...packet, status: hookStatus, semantic_disposition: hookStatus, memory_ref_digests: digests,
    decision_consumption_complete: true, preserve_base_output: hookStatus !== "applied",
  };
}
