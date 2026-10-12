/** Same-effect refresh admission, evaluated inside the settlement read transaction. */
import { createHash } from "node:crypto";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";
import { normalizeDeliveryWorkspaceSnapshot } from "../agents/delivery_workspace.ts";

import { decodeExternalDelivery, type ExternalDeliveryRequest } from "./refresh_external_delivery.ts";

export interface RefreshRetryRequest {
  first_delivery_request?: JsonObject | null;
  checkpoint_read_context_id?: string | null;
  external_delivery?: ExternalDeliveryRequest | null;
  vision: JsonObject | null;
  unchanged_reason: string | null;
  merge_patch: boolean;
  workspace_requested: boolean;
  mutation: JsonObject;
  delivery_outcome: string | null;
  delivery_batch_scale: string | null;
  delivery_boundary: string | null;
  progress_observation: JsonObject | null;
}

export function decodeRefreshRetry(value: unknown): RefreshRetryRequest | null {
  if (value === undefined || value === null) return null;
  const input = requireJsonObject(value, "refresh_retry");
  for (const key of ["merge_patch", "workspace_requested"]) {
    if (typeof input[key] !== "boolean") {
      throw new EffectRuntimeRequestError(`refresh_retry.${key} must be a boolean`);
    }
  }
  const nullableString = (key: string): string | null => {
    const value = input[key];
    if (value === null) return null;
    if (typeof value !== "string") {
      throw new EffectRuntimeRequestError(`refresh_retry.${key} must be a string or null`);
    }
    return value;
  };
  return {
    first_delivery_request: input.first_delivery_request == null ? null
      : requireJsonObject(input.first_delivery_request, "first_delivery_request"),
    checkpoint_read_context_id: input.checkpoint_read_context_id == null ? null : nullableString("checkpoint_read_context_id"),
    external_delivery: decodeExternalDelivery(input.external_delivery),
    vision: input.vision === null ? null : requireJsonObject(input.vision, "refresh_retry.vision"),
    unchanged_reason: nullableString("unchanged_reason"),
    merge_patch: input.merge_patch === true,
    workspace_requested: input.workspace_requested === true,
    mutation: requireJsonObject(input.mutation, "refresh_retry.mutation"),
    delivery_outcome: nullableString("delivery_outcome"),
    delivery_batch_scale: nullableString("delivery_batch_scale"),
    delivery_boundary: nullableString("delivery_boundary"),
    progress_observation: input.progress_observation === null ? null
      : requireJsonObject(input.progress_observation, "refresh_retry.progress_observation"),
  };
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const object = jsonObject(value);
  return object ? `{${Object.keys(object).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(object[key])}`).join(",")}}` : JSON.stringify(value);
}

type Decision = "append" | "replay" | "repair_receipt" | "supplement_checkpoint" | "supplement_workspace" | "reject";

export function isMaterialMonitorPoll(run: JsonObject | null): boolean {
  if (run?.classification !== "quota_monitor_poll") return false;
  return run.material_change === true;
}

export function refreshRecovery(
  request: RefreshRetryRequest,
  prior: JsonObject | null,
  receiptPresent: boolean,
  workspaceRequirement: string | undefined,
  laterAgentVision: boolean,
): JsonObject {
  const wantsVision = request.vision !== null || Boolean(request.unchanged_reason);
  const digest = wantsVision ? createHash("sha256").update(canonical({
    vision: request.vision,
    unchanged_reason: request.unchanged_reason,
    merge_patch: request.merge_patch,
    ...(request.checkpoint_read_context_id ? {checkpoint_read_context_id: request.checkpoint_read_context_id} : {}),
  })).digest("hex") : null;
  const mutationDigest = createHash("sha256").update(canonical(request.mutation)).digest("hex");
  const firstDigest = request.first_delivery_request == null ? null
    : createHash("sha256").update(canonical(request.first_delivery_request)).digest("hex");
  const changesMutation = Object.values(request.mutation).some((value) =>
    value !== null && value !== false && (!Array.isArray(value) || value.length > 0));
  const result = (decision: Decision, reason: string): JsonObject => ({
    schema_version: "refresh_recovery_v0",
    decision, reason, vision_request_digest: digest,
    mutation_digest: mutationDigest,
    ...(firstDigest ? {first_delivery_request_digest: firstDigest} : {}),
    ...(request.checkpoint_read_context_id ? {checkpoint_read_context_id: request.checkpoint_read_context_id} : {}),
    original_generated_at: jsonObject(prior?.refresh_recovery)?.original_generated_at
      ?? prior?.generated_at ?? null,
  });
  if (!prior) return result("append", "first_writeback");
  const priorFirstDigest = jsonObject(prior.refresh_recovery)?.first_delivery_request_digest ?? null;
  if ((firstDigest !== null || priorFirstDigest !== null) && firstDigest !== priorFirstDigest) {
    return result("reject", "committed_first_delivery_request_conflict");
  }
  const checkpoint = jsonObject(prior.vision_checkpoint);
  const priorDigest = jsonObject(prior.refresh_recovery)?.vision_request_digest;
  // Repeated CLI annotations are not mutations. Semantic delivery changes are.
  const changedDelivery = ["delivery_outcome", "delivery_batch_scale"].some((key) => {
    const requested = key === "delivery_outcome" ? request.delivery_outcome : request.delivery_batch_scale;
    return requested !== null && requested !== prior[key];
  }) || (request.delivery_boundary !== null && checkpoint !== null &&
    request.delivery_boundary !== checkpoint.delivery_boundary) ||
    (request.progress_observation !== null &&
      canonical(request.progress_observation) !== canonical(prior.progress_observation ?? null));
  const workspace = request.workspace_requested
    ? normalizeDeliveryWorkspaceSnapshot(prior.delivery_workspace) : null;
  const missingWorkspace = request.workspace_requested && workspaceRequirement !== "not_required" &&
    workspace === null;
  // A material poll is not a completed refresh: its receipt-bound first
  // workspace supplement may author next-action/vision through normal refresh
  // validation. Once appended, the recovery digests restore strict replay.
  const firstMonitorCloseout = missingWorkspace && isMaterialMonitorPoll(prior) &&
    prior.refresh_recovery == null && checkpoint === null;
  if (firstMonitorCloseout) {
    const unrelatedMutation = Object.entries(request.mutation).some(([key, value]) =>
      key !== "next_action" && value !== null && value !== false &&
      (!Array.isArray(value) || value.length > 0));
    if (unrelatedMutation ||
        (request.delivery_outcome !== null && request.delivery_outcome !== prior.delivery_outcome) ||
        (request.progress_observation !== null &&
          canonical(request.progress_observation) !== canonical(prior.progress_observation ?? null))) {
      return result("reject", "committed_writeback_payload_conflict");
    }
    if (wantsVision && laterAgentVision) return result("reject", "checkpoint_superseded_by_later_vision");
    return result("supplement_workspace", "complete_material_monitor_writeback");
  }
  if (changedDelivery ||
      (changesMutation && mutationDigest !== jsonObject(prior.refresh_recovery)?.mutation_digest)) {
    return result("reject", "committed_writeback_payload_conflict");
  }
  if (wantsVision && digest !== priorDigest) {
    if (checkpoint?.decision !== "missing_required" || checkpoint.satisfied !== false) {
      return result("reject", "committed_vision_decision_conflict");
    }
    if (laterAgentVision) return result("reject", "checkpoint_superseded_by_later_vision");
    if (changesMutation) return result("reject", "checkpoint_supplement_must_not_repeat_mutations");
    if (missingWorkspace) return result("reject", "repair_workspace_before_checkpoint");
    return result("supplement_checkpoint", "complete_missing_checkpoint_on_original_turn");
  }
  if (missingWorkspace) return result("supplement_workspace", "complete_missing_workspace_causality");
  return result(receiptPresent ? "replay" : "repair_receipt", "original_writeback_preserved");
}
