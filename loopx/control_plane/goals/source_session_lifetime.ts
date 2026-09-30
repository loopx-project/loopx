import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject } from "../runtime_decode.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "./goal_instance_identity.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

export const SOURCE_SESSION_PROFILE_ID = "source_session_v1";
export const SOURCE_SESSION_BINDING_LIMIT = 256;
export const SOURCE_SESSION_RECEIPT_LIMIT = 4096;
export const SOURCE_SESSION_LIFETIME_RECEIPT_LIMIT = 1024;

type WireGoalRef = Readonly<{
  goal_id: string;
  goal_instance_id: string;
}>;

export type SessionRejection =
  | "unsupported_profile"
  | "stale_goal_instance"
  | "session_binding_conflict"
  | "binding_capacity_exhausted"
  | "history_capacity_exhausted"
  | "operation_id_conflict";

export type SessionDecision =
  | Readonly<{
    kind: "commit";
    goal_ref: WireGoalRef;
    changed: boolean;
  }>
  | Readonly<{
    kind: "replay";
    receipt: JsonObject;
  }>
  | Readonly<{
    kind: "reject";
    code: SessionRejection;
  }>;

export type GoalRecreationRejection =
  | "unsupported_profile"
  | "stale_goal_instance"
  | "history_capacity_exhausted"
  | "session_history_capacity_exhausted"
  | "operation_id_conflict";

export type GoalRecreationDecision =
  | Readonly<{
    kind: "commit";
    retired_goal_ref: WireGoalRef;
    new_goal_ref: WireGoalRef;
  }>
  | Readonly<{
    kind: "replay";
    receipt: JsonObject;
  }>
  | Readonly<{
    kind: "reject";
    code: GoalRecreationRejection;
  }>;

type SessionBinding = Readonly<{
  sessionId: string;
  goalRef: ExactGoalRef;
}>;

type SessionBindingFacts = Readonly<{
  profileId: string;
  operationId: string;
  requestDigest: string;
  sessionId: string;
  requestedGoalRef: ExactGoalRef;
  currentGoalRef: ExactGoalRef;
  currentBinding: SessionBinding | null;
  priorReceipt: JsonObject | null;
  bindingCount: number;
  receiptCount: number;
}>;

type GoalRecreationFacts = Readonly<{
  profileId: string;
  operationId: string;
  requestDigest: string;
  requestedGoalRef: ExactGoalRef;
  currentGoalRef: ExactGoalRef;
  reservedGoalRef: ExactGoalRef;
  priorReceipt: JsonObject | null;
  lifetimeReceiptCount: number;
  sessionReceiptCount: number;
  retiringBindingCount: number;
}>;

function requiredObject(value: unknown, label: string): JsonObject {
  const result = jsonObject(value);
  if (!result) {
    throw new EffectRuntimeRequestError(`${label} must be an object`);
  }
  return result;
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  return value;
}

function requestDigest(value: unknown): string {
  const digest = requiredString(value, "request_digest");
  if (!ENVELOPED_SHA256_PATTERN.test(digest)) {
    throw new EffectRuntimeRequestError("request_digest must be a SHA-256 digest");
  }
  return digest;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-negative integer`);
  }
  return Number(value);
}

function exactGoalRef(value: unknown, label: string): ExactGoalRef {
  const parsed = parseExactGoalRef(value);
  if (parsed.kind === "invalid") {
    throw new EffectRuntimeRequestError(`${label} ${parsed.issue}`);
  }
  return parsed.value;
}

function wireGoalRef(value: ExactGoalRef): WireGoalRef {
  return {
    goal_id: value.goalId.value,
    goal_instance_id: value.goalInstanceId.value,
  };
}

function goalRefsEqual(left: ExactGoalRef, right: ExactGoalRef): boolean {
  return left.goalId.value === right.goalId.value
    && left.goalInstanceId.value === right.goalInstanceId.value;
}

function sessionBinding(value: unknown): SessionBinding | null {
  if (value === null) return null;
  const binding = requiredObject(value, "current_binding");
  return {
    sessionId: requiredString(binding.session_id, "current_binding.session_id"),
    goalRef: exactGoalRef(
      binding.foreground_goal_ref,
      "current_binding.foreground_goal_ref",
    ),
  };
}

function sessionBindingFacts(value: unknown): SessionBindingFacts {
  const facts = requiredObject(value, "source-session binding facts");
  const priorReceipt = facts.prior_receipt === null
    ? null
    : requiredObject(facts.prior_receipt, "prior_receipt");
  return {
    profileId: requiredString(facts.profile_id, "profile_id"),
    operationId: requiredString(facts.operation_id, "operation_id"),
    requestDigest: requestDigest(facts.request_digest),
    sessionId: requiredString(facts.session_id, "session_id"),
    requestedGoalRef: exactGoalRef(
      facts.requested_goal_ref,
      "requested_goal_ref",
    ),
    currentGoalRef: exactGoalRef(facts.current_goal_ref, "current_goal_ref"),
    currentBinding: sessionBinding(facts.current_binding),
    priorReceipt,
    bindingCount: nonNegativeInteger(facts.binding_count, "binding_count"),
    receiptCount: nonNegativeInteger(facts.receipt_count, "receipt_count"),
  };
}

function replaySessionDecision(
  facts: SessionBindingFacts,
  operation: "bind" | "unbind",
): SessionDecision | null {
  const receipt = facts.priorReceipt;
  if (receipt === null) return null;
  if (
    receipt.schema_version !== "loopx_source_session_receipt_v1"
    || receipt.operation !== operation
    || receipt.operation_id !== facts.operationId
    || receipt.session_id !== facts.sessionId
    || typeof receipt.changed !== "boolean"
  ) {
    throw new EffectRuntimeRequestError(`prior ${operation} receipt is malformed`);
  }
  const receiptGoalRef = exactGoalRef(receipt.goal_ref, "prior_receipt.goal_ref");
  const receiptDigest = requestDigest(receipt.request_digest);
  if (receiptDigest !== facts.requestDigest) {
    return { kind: "reject", code: "operation_id_conflict" };
  }
  if (!goalRefsEqual(receiptGoalRef, facts.requestedGoalRef)) {
    throw new EffectRuntimeRequestError("prior bind receipt goal_ref is inconsistent");
  }
  return { kind: "replay", receipt };
}

export function decideProjectSessionBind(value: unknown): SessionDecision {
  const facts = sessionBindingFacts(value);
  if (facts.profileId !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const replay = replaySessionDecision(facts, "bind");
  if (replay !== null) return replay;
  if (!goalRefsEqual(facts.requestedGoalRef, facts.currentGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  if (facts.receiptCount >= SOURCE_SESSION_RECEIPT_LIMIT) {
    return { kind: "reject", code: "history_capacity_exhausted" };
  }
  const current = facts.currentBinding;
  if (current !== null) {
    if (
      current.sessionId !== facts.sessionId
      || !goalRefsEqual(current.goalRef, facts.requestedGoalRef)
    ) {
      return { kind: "reject", code: "session_binding_conflict" };
    }
    return {
      kind: "commit",
      goal_ref: wireGoalRef(facts.requestedGoalRef),
      changed: false,
    };
  }
  if (facts.bindingCount >= SOURCE_SESSION_BINDING_LIMIT) {
    return { kind: "reject", code: "binding_capacity_exhausted" };
  }
  return {
    kind: "commit",
    goal_ref: wireGoalRef(facts.requestedGoalRef),
    changed: true,
  };
}

export function decideProjectSessionUnbind(value: unknown): SessionDecision {
  const facts = sessionBindingFacts(value);
  if (facts.profileId !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const replay = replaySessionDecision(facts, "unbind");
  if (replay !== null) return replay;
  if (!goalRefsEqual(facts.requestedGoalRef, facts.currentGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  if (facts.receiptCount >= SOURCE_SESSION_RECEIPT_LIMIT) {
    return { kind: "reject", code: "history_capacity_exhausted" };
  }
  const current = facts.currentBinding;
  if (current === null) {
    return {
      kind: "commit",
      goal_ref: wireGoalRef(facts.requestedGoalRef),
      changed: false,
    };
  }
  if (
    current.sessionId !== facts.sessionId
    || !goalRefsEqual(current.goalRef, facts.requestedGoalRef)
  ) {
    return { kind: "reject", code: "session_binding_conflict" };
  }
  return {
    kind: "commit",
    goal_ref: wireGoalRef(facts.requestedGoalRef),
    changed: true,
  };
}

function goalRecreationFacts(value: unknown): GoalRecreationFacts {
  const facts = requiredObject(value, "goal recreation facts");
  const priorReceipt = facts.prior_receipt === null
    ? null
    : requiredObject(facts.prior_receipt, "prior_receipt");
  const requestedGoalRef = exactGoalRef(
    facts.requested_goal_ref,
    "requested_goal_ref",
  );
  const reservedGoalRef = exactGoalRef(
    facts.reserved_goal_ref,
    "reserved_goal_ref",
  );
  if (
    requestedGoalRef.goalId.value !== reservedGoalRef.goalId.value
    || requestedGoalRef.goalInstanceId.value
      === reservedGoalRef.goalInstanceId.value
  ) {
    throw new EffectRuntimeRequestError(
      "reserved_goal_ref must name a new instance of the requested Goal",
    );
  }
  return {
    profileId: requiredString(facts.profile_id, "profile_id"),
    operationId: requiredString(facts.operation_id, "operation_id"),
    requestDigest: requestDigest(facts.request_digest),
    requestedGoalRef,
    currentGoalRef: exactGoalRef(facts.current_goal_ref, "current_goal_ref"),
    reservedGoalRef,
    priorReceipt,
    lifetimeReceiptCount: nonNegativeInteger(
      facts.lifetime_receipt_count,
      "lifetime_receipt_count",
    ),
    sessionReceiptCount: nonNegativeInteger(
      facts.session_receipt_count,
      "session_receipt_count",
    ),
    retiringBindingCount: nonNegativeInteger(
      facts.retiring_binding_count,
      "retiring_binding_count",
    ),
  };
}

function replayGoalRecreation(
  facts: GoalRecreationFacts,
): GoalRecreationDecision | null {
  const receipt = facts.priorReceipt;
  if (receipt === null) return null;
  if (
    receipt.schema_version !== "loopx_goal_recreation_receipt_v1"
    || receipt.operation_id !== facts.operationId
    || !Array.isArray(receipt.retired_session_ids)
    || receipt.retired_session_ids.some((value) => typeof value !== "string")
  ) {
    throw new EffectRuntimeRequestError("prior recreation receipt is malformed");
  }
  const receiptDigest = requestDigest(receipt.request_digest);
  if (receiptDigest !== facts.requestDigest) {
    return { kind: "reject", code: "operation_id_conflict" };
  }
  const retiredGoalRef = exactGoalRef(
    receipt.retired_goal_ref,
    "prior_receipt.retired_goal_ref",
  );
  const newGoalRef = exactGoalRef(
    receipt.new_goal_ref,
    "prior_receipt.new_goal_ref",
  );
  if (
    !goalRefsEqual(retiredGoalRef, facts.requestedGoalRef)
    || !goalRefsEqual(newGoalRef, facts.reservedGoalRef)
  ) {
    throw new EffectRuntimeRequestError(
      "prior recreation receipt Goal references are inconsistent",
    );
  }
  return { kind: "replay", receipt };
}

export function decideGoalRecreation(value: unknown): GoalRecreationDecision {
  const facts = goalRecreationFacts(value);
  if (facts.profileId !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const replay = replayGoalRecreation(facts);
  if (replay !== null) return replay;
  if (!goalRefsEqual(facts.requestedGoalRef, facts.currentGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  if (
    facts.lifetimeReceiptCount >= SOURCE_SESSION_LIFETIME_RECEIPT_LIMIT
  ) {
    return { kind: "reject", code: "history_capacity_exhausted" };
  }
  if (
    facts.sessionReceiptCount + facts.retiringBindingCount
      > SOURCE_SESSION_RECEIPT_LIMIT
  ) {
    return {
      kind: "reject",
      code: "session_history_capacity_exhausted",
    };
  }
  return {
    kind: "commit",
    retired_goal_ref: wireGoalRef(facts.requestedGoalRef),
    new_goal_ref: wireGoalRef(facts.reservedGoalRef),
  };
}
