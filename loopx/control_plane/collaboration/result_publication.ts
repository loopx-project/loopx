import { createHash } from "node:crypto";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireStringLiteral } from "../runtime_decode.ts";
import { receiverDecision, sameRequest, workspaceRef } from "./semantic_request.ts";
import {requestRevisionReceiptContext} from "./request_control.ts";

// Local storage keys, not task status or authority. Updates keep the existing
// conclusion phase and append immutable observations in publication order.
const UPDATE_KEY = /^conclusion-[0-9]{8}$/;
const UPDATE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/;

export function resultAttachmentRefs(params: JsonObject): JsonObject {
  if (params.workspace_current === false) {
    throw new EffectRuntimeRequestError("result file workspace Goal instance changed");
  }
  if (!Array.isArray(params.refs) || params.refs.length > 4) {
    throw new EffectRuntimeRequestError("at most four result attachments are allowed");
  }
  const refs = params.refs.map(workspaceRef);
  if (new Set(refs).size !== refs.length) throw new EffectRuntimeRequestError("duplicate result attachment refs");
  return {refs};
}

/** Explicit result files are content-bound observations, never new grants. */
export function resultAttachments(value: unknown): JsonObject[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 4) {
    throw new EffectRuntimeRequestError("at most four result attachments are allowed");
  }
  let total = 0;
  const files = value.map((raw) => {
    const file = requireJsonObject(raw, "result attachment");
    if (Object.keys(file).sort().join(",") !== "name,ref,sha256,size") {
      throw new EffectRuntimeRequestError("invalid result attachment fields");
    }
    const ref = workspaceRef(file.ref);
    if (file.name !== ref.split("/").at(-1)
        || typeof file.sha256 !== "string" || !BARE_SHA256_PATTERN.test(file.sha256)
        || typeof file.size !== "number" || !Number.isSafeInteger(file.size)
        || file.size < 1 || file.size > 30 * 1024 * 1024) {
      throw new EffectRuntimeRequestError("invalid result attachment content");
    }
    total += file.size;
    return {ref, name: file.name, sha256: file.sha256, size: file.size};
  });
  if (total > 60 * 1024 * 1024 || new Set(files.map((f) => f.ref)).size !== files.length) {
    throw new EffectRuntimeRequestError("duplicate or oversized result attachments");
  }
  return files;
}

export function collaborationResultObservations(request: JsonObject, observations: unknown,
    decision: string | null, legacyAssessment: unknown = null) {
  if (!Array.isArray(observations)) throw new EffectRuntimeRequestError("result observations are required");
  const results = observations.map((item) => {
    const record = requireJsonObject(item, "result observation");
    const value = requireJsonObject(record.value, "result receipt");
    const historical = (request.request_control !== undefined || value.request_revision !== undefined) ? requestRevisionReceiptContext(request, value.request_revision, legacyAssessment) : null;
    if (typeof record.key !== "string" || !sameRequest(value, request, true)
        || value.decision !== (historical === null ? decision : (historical.assessment as JsonObject | null)?.decision)
        || (value.request_revision_digest !== undefined && value.request_revision_digest !== historical?.revision_digest)
        || typeof record.text_chars !== "number"
        || !Number.isSafeInteger(record.text_chars) || record.text_chars < 1 || record.text_chars > 20000
        || typeof record.text_sha256 !== "string" || !BARE_SHA256_PATTERN.test(record.text_sha256)) {
      throw new EffectRuntimeRequestError("result receipt identity or content conflict");
    }
    return { key: record.key, value, textSha256: record.text_sha256,
      attachments: resultAttachments(value.attachments) };
  });
  if (new Set(results.map((r) => r.key)).size !== results.length) {
    throw new EffectRuntimeRequestError("duplicate result observation");
  }
  let previous = "conclusion";
  const updates = results.filter((r) => UPDATE_KEY.test(r.key)).sort((a, b) => a.key.localeCompare(b.key));
  const ids = new Set<string>();
  for (const [index, receipt] of updates.entries()) {
    if (receipt.key !== `conclusion-${String(index + 1).padStart(8, "0")}`
        || receipt.value.phase !== "conclusion" || receipt.value.result_key !== receipt.key
        || receipt.value.previous_result_key !== previous
        || typeof receipt.value.update_id !== "string" || !UPDATE_ID.test(receipt.value.update_id)
        || ids.has(receipt.value.update_id)) {
      throw new EffectRuntimeRequestError("result update chain conflict");
    }
    ids.add(receipt.value.update_id);
    previous = receipt.key;
  }
  for (const receipt of results.filter((r) => !UPDATE_KEY.test(r.key))) {
    if (!["decision", "conclusion"].includes(receipt.key) || receipt.value.phase !== receipt.key) {
      throw new EffectRuntimeRequestError("result receipt identity conflict");
    }
  }
  return results;
}

export function planCollaborationResult(params: JsonObject): JsonObject {
  const request = requireJsonObject(params.request, "result request");
  const phase = requireStringLiteral(params.phase, ["decision", "conclusion"], "result phase");
  const controlled = request.request_control !== undefined || params.request_revision != null;
  // Old commands always name original revision 1. They never inherit the new
  // head merely because a correction arrived while their result was in flight.
  const context = controlled ? requestRevisionReceiptContext(request, params.request_revision, params.legacy_assessment ?? null) : null;
  const decision = receiverDecision(context === null ? params.decision : (context.assessment as JsonObject | null)?.decision);
  const attachments = resultAttachments(params.attachments);
  if (decision === null) throw new EffectRuntimeRequestError("a receiver decision is required");
  if (typeof params.text !== "string" || !params.text.trim() || Array.from(params.text).length > 20000) {
    throw new EffectRuntimeRequestError("bounded reply text is required");
  }
  const results = collaborationResultObservations(request, params.results, decision, params.legacy_assessment ?? null);
  const updates = results.filter((r) => UPDATE_KEY.test(r.key)).sort((a, b) => a.key.localeCompare(b.key));
  const previous = updates.at(-1)?.key ?? "conclusion";
  const conclusion = results.find((r) => r.key === "conclusion");
  if (updates.length && !conclusion) throw new EffectRuntimeRequestError("initial conclusion is required");
  const revision = context?.revision ?? 1;
  const primaryUpdate = `request-revision-${revision}`;
  const initialForRevision = conclusion && (conclusion.value.request_revision ?? 1) === revision;
  const updateId = params.update_id ?? (controlled && phase === "conclusion" && conclusion && !initialForRevision ? primaryUpdate : null);
  const isUpdate = updateId !== undefined && updateId !== null;
  if (isUpdate && (typeof updateId !== "string" || !UPDATE_ID.test(updateId))) {
    throw new EffectRuntimeRequestError("a bounded update id is required");
  }
  if (isUpdate && (phase !== "conclusion" || !conclusion)) {
    throw new EffectRuntimeRequestError("a result update requires an initial conclusion");
  }
  const old = isUpdate ? updates.find((r) => r.value.update_id === updateId) : results.find((r) => r.key === phase);
  const value: JsonObject = Object.fromEntries(["request_id", "goal_id", "agent_id", "source_id", "goal_ref"]
    .filter((key) => request[key] !== undefined).map((key) => [key, request[key]]));
  Object.assign(value, { phase, decision, text: params.text.trim() });
  if (context !== null) Object.assign(value, {request_revision: context.revision, request_revision_digest: context.revision_digest});
  if (attachments.length) value.attachments = attachments;
  const key = old?.key ?? (isUpdate ? `conclusion-${String(updates.length + 1).padStart(8, "0")}` : phase);
  if (isUpdate) Object.assign(value, { result_key: key, update_id: updateId, previous_result_key: old?.value.previous_result_key ?? previous });
  if (old && (old.textSha256 !== createHash("sha256").update(String(value.text)).digest("hex") || old.value.phase !== phase
      || (old.value.request_revision ?? 1) !== revision
      || JSON.stringify(old.attachments) !== JSON.stringify(attachments))) {
    throw new EffectRuntimeRequestError("reply already committed; conflicting replacement rejected; use a stable update_id for a later conclusion");
  }
  if (!old && phase === "decision" && conclusion && (conclusion.value.request_revision ?? 1) === revision) {
    throw new EffectRuntimeRequestError("cannot publish an intermediate decision after conclusion");
  }
  return { result_key: key, value, replay: old !== undefined,
    ...(context === null ? {} : {request_revision: context.revision, current_request_revision: context.current_revision, request_brief: context.brief}) };
}

/** Transport observations cannot let an update overtake an uncertain send. */
export function collaborationResultDeliveryReady(params: JsonObject): JsonObject {
  const reply = requireJsonObject(params.result, "result receipt");
  const previous = reply.previous_result_key;
  if (previous === undefined) return { ready: true };
  if (previous !== "conclusion" && (typeof previous !== "string" || !UPDATE_KEY.test(previous))) {
    throw new EffectRuntimeRequestError("invalid previous result key");
  }
  const delivery = params.previous_delivery === null ? null
    : requireJsonObject(params.previous_delivery, "previous result delivery");
  return { ready: delivery?.status === "delivered"
    && (previous === "conclusion" || delivery.result_key === previous) };
}
