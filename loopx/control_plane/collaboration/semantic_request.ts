import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import { isDeepStrictEqual } from "node:util";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";

export const RECEIVER_DECISIONS = ["adopt", "defer", "reject", "no_change"] as const;
export type ReceiverDecision = typeof RECEIVER_DECISIONS[number];

const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/;

export const ORIGINAL_CONVERSATION_RECIPIENT_SCHEMA = "collaboration_original_conversation_recipient_v0";
export const CONVERSATION_ENTRY_SCHEMA = "loopx_collaboration_conversation_entry_v0";

/** An explicit original Session address, never a synthetic Goal or Agent. */
export function normalizeOriginalConversationRecipient(value: unknown): JsonObject {
  const recipient = requireJsonObject(value, "original conversation recipient");
  exactKeys(recipient, ["schema_version", "binding_id", "session_id", "identity_sha256"],
    ["schema_version", "binding_id", "session_id", "identity_sha256"]);
  if (recipient.schema_version !== ORIGINAL_CONVERSATION_RECIPIENT_SCHEMA
      || typeof recipient.binding_id !== "string" || !/^[a-f0-9]{24}$/.test(recipient.binding_id)
      || typeof recipient.session_id !== "string" || !ID.test(recipient.session_id)
      || typeof recipient.identity_sha256 !== "string" || !BARE_SHA256_PATTERN.test(recipient.identity_sha256)) {
    throw new EffectRuntimeRequestError("invalid original conversation recipient");
  }
  return {...recipient};
}

export function originalConversationReturnRoute(entry: JsonObject): JsonObject {
  if (entry.schema_version !== CONVERSATION_ENTRY_SCHEMA) throw new EffectRuntimeRequestError("original conversation return profile is required");
  normalizeOriginalConversationRecipient(entry.recipient);
  return {kind: "peer", channel_id: "peer", ...Object.fromEntries([
    "request_id", "source_id", "source_agent_id", "requester", "recipient"].map(key => [key, entry[key]]))};
}

export function verifyOriginalConversationReturnRoute(params: JsonObject): JsonObject {
  const expected = originalConversationReturnRoute(requireJsonObject(params.entry, "original conversation request"));
  if (!isDeepStrictEqual(expected, params.route)) throw new EffectRuntimeRequestError("original requester return route identity changed");
  return {verified: true};
}

function text(value: unknown, label: string, limit: number): string {
  const result = requireNonEmptyString(value, label).trim();
  if (result.length > limit) throw new EffectRuntimeRequestError(`${label} exceeds ${limit} characters`);
  return result;
}

function exactKeys(value: JsonObject, allowed: string[], required: string[]): void {
  if (Object.keys(value).some((key) => !allowed.includes(key))
      || required.some((key) => !(key in value))) {
    throw new EffectRuntimeRequestError("collaboration request has unsupported or missing fields");
  }
}

function lines(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 12) {
    throw new EffectRuntimeRequestError(`${label} must contain at most 12 items`);
  }
  return value.map((item, index) => text(item, `${label}[${index}]`, 1000));
}

export function workspaceRef(value: unknown): string {
  const ref = text(value, "input.ref", 512);
  // References never fetch URLs or expose a sender's absolute filesystem path.
  if (ref.startsWith("/") || ref.includes("\\") || ref.includes(":")
      || ref.split("/").some((part) => !part || part === "." || part === "..")
      || /[\x00-\x1f]/.test(ref)) {
    throw new EffectRuntimeRequestError("input.ref must be a relative workspace file path");
  }
  return ref;
}

/** Semantic content is context, never a Todo edit, execution grant or priority. */
export function normalizeCollaborationBrief(value: unknown): JsonObject {
  const brief = requireJsonObject(value, "brief");
  if (new TextEncoder().encode(JSON.stringify(brief)).length > 16000) {
    throw new EffectRuntimeRequestError("collaboration brief exceeds 16000 bytes");
  }
  const keys = ["schema_version", "purpose", "context", "constraints", "inputs", "acceptance", "return_requirement"];
  exactKeys(brief, keys, keys);
  if (brief.schema_version !== "collaboration_brief_v0") {
    throw new EffectRuntimeRequestError("unsupported collaboration brief schema");
  }
  if (!Array.isArray(brief.inputs) || brief.inputs.length > 12) {
    throw new EffectRuntimeRequestError("brief.inputs must contain at most 12 items");
  }
  const inputs = brief.inputs.map((raw) => {
    const input = requireJsonObject(raw, "brief input");
    exactKeys(input, ["ref", "description", "sha256", "delegation"], ["ref", "description"]);
    const ref = workspaceRef(input.ref);
    const result: JsonObject = { ref, description: text(input.description, "input.description", 1000) };
    if (input.sha256 !== undefined) {
      if (typeof input.sha256 !== "string" || !BARE_SHA256_PATTERN.test(input.sha256)) {
        throw new EffectRuntimeRequestError("input.sha256 must be a SHA256 digest");
      }
      result.sha256 = input.sha256;
    }
    if (input.delegation !== undefined) {
      const source = requireJsonObject(input.delegation, "input.delegation");
      exactKeys(source, ["operation_id", "ref", "relation"], ["operation_id", "ref", "relation"]);
      if (typeof source.operation_id !== "string" || !ID.test(source.operation_id)
          || !["responds_to", "revises", "uses"].includes(String(source.relation)) || !result.sha256) {
        throw new EffectRuntimeRequestError("delegation input requires operation, artifact, relation and digest");
      }
      result.delegation = {...source, ref: workspaceRef(source.ref)};
    }
    return result;
  });
  const acceptance = lines(brief.acceptance, "brief.acceptance");
  if (!acceptance.length) throw new EffectRuntimeRequestError("brief.acceptance must name an observable result");
  return {
    schema_version: "collaboration_brief_v0",
    purpose: text(brief.purpose, "brief.purpose", 2000),
    context: text(brief.context, "brief.context", 6000),
    constraints: lines(brief.constraints, "brief.constraints"),
    inputs, acceptance,
    return_requirement: text(brief.return_requirement, "brief.return_requirement", 2000),
  };
}

export function normalizeCollaborationRequest(value: unknown): JsonObject {
  const request = requireJsonObject(value, "request");
  if (Object.hasOwn(request, "recipient")) {
    exactKeys(request, ["recipient", "brief"], ["recipient", "brief"]);
    return {recipient: normalizeOriginalConversationRecipient(request.recipient),
      brief: normalizeCollaborationBrief(request.brief)};
  }
  exactKeys(request, ["goal_id", "agent_id", "brief", "execution_binding_id"], ["goal_id", "agent_id"]);
  const result: JsonObject = {};
  for (const key of ["goal_id", "agent_id"]) {
    const id = requireNonEmptyString(request[key], key);
    if (!ID.test(id)) throw new EffectRuntimeRequestError(`invalid collaboration ${key}`);
    result[key] = id;
  }
  if (request.brief !== undefined) result.brief = normalizeCollaborationBrief(request.brief);
  // Selection is data, not a grant. The host separately rechecks the source,
  // operator binding, canonical task, acceptance and governed Turn admission.
  if (request.execution_binding_id !== undefined) {
    if (!result.brief || typeof request.execution_binding_id !== "string" || !ID.test(request.execution_binding_id)) {
      throw new EffectRuntimeRequestError("execution selection requires an exact binding and semantic brief");
    }
    result.execution_binding_id = request.execution_binding_id;
  }
  return result;
}

/** Preserve source text while leaving room in the existing 128 KB inbox record. */
export function normalizeCollaborationSourceContext(input: JsonObject): JsonObject {
  const source = requireNonEmptyString(input.source_message, "source_message");
  if (Array.from(source).length > 32000) {
    throw new EffectRuntimeRequestError("collaboration source context exceeds 32000 characters; use a scoped artifact reference for larger material");
  }
  // JSON escaping and UTF-8 differ from character count; reserve the rest for
  // the existing semantic brief, request identity and receiver instructions.
  if (new TextEncoder().encode(JSON.stringify(source)).length > 98304) {
    throw new EffectRuntimeRequestError("collaboration source context exceeds 98304 encoded bytes; use a scoped artifact reference for larger material");
  }
  return {source_message: source};
}

/** One publication decision for both ingress adapters, under their request lock.
 * Generated guidance is historical delivery metadata, never request identity.
 * The original operation also supplies guidance when recovering a missing entry.
 */
export function planCollaborationRequestPublication(params: JsonObject): JsonObject {
  const request = requireJsonObject(params.request, "request");
  const entry = params.entry === null ? null : requireJsonObject(params.entry, "entry");
  const operation = params.operation === null ? null : requireJsonObject(params.operation, "operation");
  const peer = request.source_kind === "peer";
  if ((!peer && request.source_kind !== undefined) || (!peer && operation !== null)
      || "delivered_at" in request || "source_channel" in request) {
    throw new EffectRuntimeRequestError("invalid request publication source");
  }
  const content = (value: JsonObject, entryMetadata: boolean): JsonObject => Object.fromEntries(
    Object.entries(value).filter(([key]) => key !== "instruction"
      && !(entryMetadata && (key === "delivered_at" || (!peer && key === "source_channel")))),
  );
  const expected = content(request, false);
  if (operation !== null && !isDeepStrictEqual(content(operation, false), expected)) {
    throw new EffectRuntimeRequestError("peer request operation identity conflict");
  }
  if (entry !== null && !isDeepStrictEqual(content(entry, true), expected)) {
    throw new EffectRuntimeRequestError(peer ? "peer request identity conflict" : "context request identity conflict");
  }
  const original = operation ?? request;
  return {
    replayed: entry !== null,
    operation: peer ? original : null,
    entry: entry ?? original,
  };
}

function sameGoalRef(left: unknown, right: unknown): boolean {
  if (left === undefined && right === undefined) return true;
  const a = parseExactGoalRef(left);
  const b = parseExactGoalRef(right);
  return a.kind === "parsed" && b.kind === "parsed"
    && a.value.goalId.value === b.value.goalId.value
    && a.value.goalInstanceId.value === b.value.goalInstanceId.value;
}

export function sameRequest(receipt: JsonObject | null, request: JsonObject, source: boolean): receipt is JsonObject {
  if (Object.hasOwn(request, "recipient") || receipt && Object.hasOwn(receipt, "recipient")) {
    return receipt !== null && request.schema_version === CONVERSATION_ENTRY_SCHEMA
      && receipt.request_id === request.request_id
      && isDeepStrictEqual(receipt.recipient, request.recipient)
      && isDeepStrictEqual(receipt.requester, request.requester)
      && (!source || receipt.source_id === request.source_id);
  }
  return receipt !== null && ["request_id", "goal_id", "agent_id"].every((key) => receipt[key] === request[key])
    && sameGoalRef(receipt.goal_ref, request.goal_ref)
    && (!source || receipt.source_id === request.source_id);
}

export function receiverDecision(value: unknown): ReceiverDecision | null {
  switch (value) {
    case "adopt": case "defer": case "reject": case "no_change": return value;
    default: return null;
  }
}
