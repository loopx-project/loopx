import {
  TURN_PROVIDER_STEP_KINDS,
  type JsonObject,
  type TurnProviderStepKind,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireStringLiteral } from "../runtime_decode.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "./goal_instance_identity.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

export const SOURCE_SESSION_PROFILE_ID = "source_session_v1";
export const SOURCE_SESSION_BINDING_LIMIT = 256;
export const SOURCE_SESSION_RECEIPT_LIMIT = 4096;
export const SOURCE_SESSION_LIFETIME_RECEIPT_LIMIT = 1024;
export const SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION =
  "loopx_source_turn_effect_gate_v1";
export const SOURCE_TURN_EFFECT_ADMISSION_SCHEMA_VERSION =
  "loopx_source_turn_effect_admission_v1";

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

type SourceTurnEffectAdmission = Readonly<{
  schema_version: typeof SOURCE_TURN_EFFECT_ADMISSION_SCHEMA_VERSION;
  goal_ref: WireGoalRef;
  turn_key: string;
  step_kind: TurnProviderStepKind;
  effect_ref: string;
}>;

type SourceTurnEffectOpenGate = Readonly<{
  schema_version: typeof SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION;
  state: "open";
  goal_ref: WireGoalRef;
}>;

type SourceTurnEffectClosingGate = Readonly<{
  schema_version: typeof SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION;
  state: "closing";
  retired_goal_ref: WireGoalRef;
  new_goal_ref: WireGoalRef;
  operation_id: string;
  request_digest: string;
}>;

type SourceTurnEffectGate =
  | SourceTurnEffectOpenGate
  | SourceTurnEffectClosingGate;

export type SourceTurnEffectRejection =
  | "unsupported_profile"
  | "stale_goal_instance"
  | "goal_retirement_in_progress"
  | "effect_admission_conflict"
  | "effect_drain_required"
  | "recreation_operation_conflict";

type SourceTurnEffectReject = Readonly<{
  kind: "reject";
  code: SourceTurnEffectRejection;
}>;

export type SourceTurnEffectAdmissionDecision =
  | Readonly<{
    kind: "commit";
    gate: SourceTurnEffectGate;
    admission: SourceTurnEffectAdmission;
  }>
  | Readonly<{
    kind: "replay";
    gate: SourceTurnEffectGate;
    admission: SourceTurnEffectAdmission;
  }>
  | SourceTurnEffectReject;

export type SourceTurnEffectReleaseDecision =
  | Readonly<{ kind: "commit" }>
  | Readonly<{ kind: "replay" }>
  | SourceTurnEffectReject;

export type SourceTurnEffectAbsentDecision =
  | Readonly<{ kind: "execute" }>
  | Readonly<{ kind: "abort" }>
  | SourceTurnEffectReject;

export type SourceTurnEffectGateDecision =
  | Readonly<{
    kind: "commit";
    gate: SourceTurnEffectGate;
  }>
  | Readonly<{
    kind: "replay";
    gate: SourceTurnEffectGate;
  }>
  | Readonly<{
    kind: "preserve";
  }>
  | SourceTurnEffectReject;

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

function sourceTurnEffectStep(value: unknown): TurnProviderStepKind {
  return requireStringLiteral(
    value,
    TURN_PROVIDER_STEP_KINDS,
    "step_kind",
  );
}

function sourceTurnEffectAdmission(
  value: unknown,
  label: string,
): SourceTurnEffectAdmission {
  const admission = requiredObject(value, label);
  if (
    admission.schema_version !== SOURCE_TURN_EFFECT_ADMISSION_SCHEMA_VERSION
  ) {
    throw new EffectRuntimeRequestError(`${label} schema_version is unsupported`);
  }
  return {
    schema_version: SOURCE_TURN_EFFECT_ADMISSION_SCHEMA_VERSION,
    goal_ref: wireGoalRef(exactGoalRef(admission.goal_ref, `${label}.goal_ref`)),
    turn_key: requestDigest(admission.turn_key),
    step_kind: sourceTurnEffectStep(admission.step_kind),
    effect_ref: requiredString(admission.effect_ref, `${label}.effect_ref`),
  };
}

function sourceTurnEffectGate(value: unknown): SourceTurnEffectGate | null {
  if (value === null) return null;
  const gate = requiredObject(value, "gate");
  if (gate.schema_version !== SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("gate schema_version is unsupported");
  }
  if (gate.state === "open") {
    return {
      schema_version: SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION,
      state: "open",
      goal_ref: wireGoalRef(exactGoalRef(gate.goal_ref, "gate.goal_ref")),
    };
  }
  if (gate.state === "closing") {
    const retiredGoalRef = exactGoalRef(
      gate.retired_goal_ref,
      "gate.retired_goal_ref",
    );
    const newGoalRef = exactGoalRef(gate.new_goal_ref, "gate.new_goal_ref");
    if (
      retiredGoalRef.goalId.value !== newGoalRef.goalId.value
      || retiredGoalRef.goalInstanceId.value === newGoalRef.goalInstanceId.value
    ) {
      throw new EffectRuntimeRequestError(
        "closing gate must reserve a new instance of the retired Goal",
      );
    }
    return {
      schema_version: SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION,
      state: "closing",
      retired_goal_ref: wireGoalRef(retiredGoalRef),
      new_goal_ref: wireGoalRef(newGoalRef),
      operation_id: requiredString(gate.operation_id, "gate.operation_id"),
      request_digest: requestDigest(gate.request_digest),
    };
  }
  throw new EffectRuntimeRequestError("gate state is unsupported");
}

function admissionEqual(
  left: SourceTurnEffectAdmission,
  right: SourceTurnEffectAdmission,
): boolean {
  return left.schema_version === right.schema_version
    && left.goal_ref.goal_id === right.goal_ref.goal_id
    && left.goal_ref.goal_instance_id === right.goal_ref.goal_instance_id
    && left.turn_key === right.turn_key
    && left.step_kind === right.step_kind
    && left.effect_ref === right.effect_ref;
}

function openSourceTurnEffectGate(goalRef: ExactGoalRef): SourceTurnEffectOpenGate {
  return {
    schema_version: SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION,
    state: "open",
    goal_ref: wireGoalRef(goalRef),
  };
}

export function decideSourceTurnEffectAdmission(
  value: unknown,
): SourceTurnEffectAdmissionDecision {
  const facts = requiredObject(value, "source Turn effect admission facts");
  if (requiredString(facts.profile_id, "profile_id") !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const requestedGoalRef = exactGoalRef(
    facts.requested_goal_ref,
    "requested_goal_ref",
  );
  const currentGoalRef = exactGoalRef(facts.current_goal_ref, "current_goal_ref");
  if (!goalRefsEqual(requestedGoalRef, currentGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  const admission = sourceTurnEffectAdmission(facts.admission, "admission");
  const admissionGoalRef = exactGoalRef(admission.goal_ref, "admission.goal_ref");
  if (!goalRefsEqual(admissionGoalRef, requestedGoalRef)) {
    throw new EffectRuntimeRequestError(
      "admission.goal_ref must match requested_goal_ref",
    );
  }
  const existing = facts.existing_admission === null
    ? null
    : sourceTurnEffectAdmission(
      facts.existing_admission,
      "existing_admission",
    );
  const gate = sourceTurnEffectGate(facts.gate);
  if (gate?.state === "closing") {
    const retiredGoalRef = exactGoalRef(
      gate.retired_goal_ref,
      "gate.retired_goal_ref",
    );
    if (
      existing !== null
      && admissionEqual(existing, admission)
      && goalRefsEqual(retiredGoalRef, requestedGoalRef)
    ) {
      return { kind: "replay", gate, admission };
    }
    return { kind: "reject", code: "goal_retirement_in_progress" };
  }
  if (
    gate?.state === "open"
    && (
      gate.goal_ref.goal_id !== requestedGoalRef.goalId.value
      || gate.goal_ref.goal_instance_id !== requestedGoalRef.goalInstanceId.value
    )
  ) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  const openedGate = gate ?? openSourceTurnEffectGate(requestedGoalRef);
  if (existing === null) {
    return { kind: "commit", gate: openedGate, admission };
  }
  return admissionEqual(existing, admission)
    ? { kind: "replay", gate: openedGate, admission }
    : { kind: "reject", code: "effect_admission_conflict" };
}

export function decideSourceTurnEffectRelease(
  value: unknown,
): SourceTurnEffectReleaseDecision {
  const facts = requiredObject(value, "source Turn effect release facts");
  if (requiredString(facts.profile_id, "profile_id") !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const requested = sourceTurnEffectAdmission(facts.admission, "admission");
  const requestedGoalRef = exactGoalRef(requested.goal_ref, "admission.goal_ref");
  const currentGoalRef = exactGoalRef(facts.current_goal_ref, "current_goal_ref");
  if (!goalRefsEqual(requestedGoalRef, currentGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  const gate = sourceTurnEffectGate(facts.gate);
  if (gate === null) {
    throw new EffectRuntimeRequestError("source Turn effect gate is missing");
  }
  const gateGoalRef = exactGoalRef(
    gate.state === "open" ? gate.goal_ref : gate.retired_goal_ref,
    "gate GoalRef",
  );
  if (!goalRefsEqual(gateGoalRef, requestedGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  if (facts.existing_admission === null) {
    return { kind: "replay" };
  }
  const existing = sourceTurnEffectAdmission(
    facts.existing_admission,
    "existing_admission",
  );
  return admissionEqual(existing, requested)
    ? { kind: "commit" }
    : { kind: "reject", code: "effect_admission_conflict" };
}

export function decideSourceTurnEffectAbsentResolution(
  value: unknown,
): SourceTurnEffectAbsentDecision {
  const facts = requiredObject(
    value,
    "source Turn effect absent-resolution facts",
  );
  if (requiredString(facts.profile_id, "profile_id") !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const requested = sourceTurnEffectAdmission(facts.admission, "admission");
  const existing = facts.existing_admission === null
    ? null
    : sourceTurnEffectAdmission(
      facts.existing_admission,
      "existing_admission",
    );
  if (existing === null || !admissionEqual(existing, requested)) {
    return { kind: "reject", code: "effect_admission_conflict" };
  }
  const requestedGoalRef = exactGoalRef(requested.goal_ref, "admission.goal_ref");
  const currentGoalRef = exactGoalRef(facts.current_goal_ref, "current_goal_ref");
  if (!goalRefsEqual(requestedGoalRef, currentGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  const gate = sourceTurnEffectGate(facts.gate);
  if (gate === null) {
    throw new EffectRuntimeRequestError("source Turn effect gate is missing");
  }
  const gateGoalRef = exactGoalRef(
    gate.state === "open" ? gate.goal_ref : gate.retired_goal_ref,
    "gate GoalRef",
  );
  if (!goalRefsEqual(gateGoalRef, requestedGoalRef)) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  return gate.state === "closing" ? { kind: "abort" } : { kind: "execute" };
}

export function decideSourceTurnEffectGate(
  value: unknown,
): SourceTurnEffectGateDecision {
  const facts = requiredObject(value, "source Turn effect gate facts");
  if (requiredString(facts.profile_id, "profile_id") !== SOURCE_SESSION_PROFILE_ID) {
    return { kind: "reject", code: "unsupported_profile" };
  }
  const operation = requiredString(facts.operation, "operation");
  const requestedGoalRef = exactGoalRef(
    facts.requested_goal_ref,
    "requested_goal_ref",
  );
  const currentGoalRef = exactGoalRef(facts.current_goal_ref, "current_goal_ref");
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
  const operationId = requiredString(facts.operation_id, "operation_id");
  const digest = requestDigest(facts.request_digest);
  const gate = sourceTurnEffectGate(facts.gate);
  const closingGate: SourceTurnEffectClosingGate = {
    schema_version: SOURCE_TURN_EFFECT_GATE_SCHEMA_VERSION,
    state: "closing",
    retired_goal_ref: wireGoalRef(requestedGoalRef),
    new_goal_ref: wireGoalRef(reservedGoalRef),
    operation_id: operationId,
    request_digest: digest,
  };
  const sameClosingGate = gate?.state === "closing"
    && gate.retired_goal_ref.goal_id === closingGate.retired_goal_ref.goal_id
    && gate.retired_goal_ref.goal_instance_id
      === closingGate.retired_goal_ref.goal_instance_id
    && gate.new_goal_ref.goal_id === closingGate.new_goal_ref.goal_id
    && gate.new_goal_ref.goal_instance_id
      === closingGate.new_goal_ref.goal_instance_id
    && gate.operation_id === operationId
    && gate.request_digest === digest;

  if (operation === "close") {
    if (
      !goalRefsEqual(requestedGoalRef, currentGoalRef)
      && !(sameClosingGate && goalRefsEqual(reservedGoalRef, currentGoalRef))
    ) {
      return { kind: "reject", code: "stale_goal_instance" };
    }
    if (gate?.state === "closing") {
      return sameClosingGate
        ? { kind: "replay", gate: closingGate }
        : { kind: "reject", code: "recreation_operation_conflict" };
    }
    if (
      gate?.state === "open"
      && (
        gate.goal_ref.goal_id !== requestedGoalRef.goalId.value
        || gate.goal_ref.goal_instance_id
          !== requestedGoalRef.goalInstanceId.value
      )
    ) {
      return { kind: "reject", code: "stale_goal_instance" };
    }
    return { kind: "commit", gate: closingGate };
  }
  const admissionCount = nonNegativeInteger(
    facts.admission_count,
    "admission_count",
  );
  if (operation === "repair") {
    if (
      sameClosingGate
      && goalRefsEqual(reservedGoalRef, currentGoalRef)
    ) {
      return admissionCount === 0
        ? {
          kind: "replay",
          gate: openSourceTurnEffectGate(reservedGoalRef),
        }
        : { kind: "reject", code: "effect_drain_required" };
    }
    if (gate?.state === "open") {
      const gateGoalRef = exactGoalRef(gate.goal_ref, "gate.goal_ref");
      if (goalRefsEqual(gateGoalRef, requestedGoalRef)) {
        return { kind: "reject", code: "recreation_operation_conflict" };
      }
      return gateGoalRef.goalId.value === requestedGoalRef.goalId.value
          && goalRefsEqual(gateGoalRef, currentGoalRef)
        ? { kind: "preserve" }
        : { kind: "reject", code: "recreation_operation_conflict" };
    }
    if (gate?.state === "closing") {
      const retiredGateRef = exactGoalRef(
        gate.retired_goal_ref,
        "gate.retired_goal_ref",
      );
      const newGateRef = exactGoalRef(gate.new_goal_ref, "gate.new_goal_ref");
      if (goalRefsEqual(retiredGateRef, requestedGoalRef)) {
        return { kind: "reject", code: "recreation_operation_conflict" };
      }
      return (
          retiredGateRef.goalId.value === requestedGoalRef.goalId.value
          && (
            goalRefsEqual(retiredGateRef, currentGoalRef)
            || goalRefsEqual(newGateRef, currentGoalRef)
          )
        )
        ? { kind: "preserve" }
        : { kind: "reject", code: "recreation_operation_conflict" };
    }
    if (
      gate === null
      && admissionCount === 0
      && goalRefsEqual(reservedGoalRef, currentGoalRef)
    ) {
      return {
        kind: "replay",
        gate: openSourceTurnEffectGate(reservedGoalRef),
      };
    }
    return { kind: "reject", code: "recreation_operation_conflict" };
  }
  if (operation !== "publish") {
    throw new EffectRuntimeRequestError(
      "operation must be close, publish, or repair",
    );
  }
  if (
    gate === null
    && admissionCount === 0
    && goalRefsEqual(reservedGoalRef, currentGoalRef)
  ) {
    return {
      kind: "replay",
      gate: openSourceTurnEffectGate(reservedGoalRef),
    };
  }
  if (gate?.state === "open") {
    return (
        gate.goal_ref.goal_id === reservedGoalRef.goalId.value
        && gate.goal_ref.goal_instance_id === reservedGoalRef.goalInstanceId.value
        && goalRefsEqual(reservedGoalRef, currentGoalRef)
      )
      ? { kind: "replay", gate }
      : { kind: "reject", code: "recreation_operation_conflict" };
  }
  if (!sameClosingGate) {
    return { kind: "reject", code: "recreation_operation_conflict" };
  }
  if (admissionCount !== 0) {
    return { kind: "reject", code: "effect_drain_required" };
  }
  if (
    !goalRefsEqual(requestedGoalRef, currentGoalRef)
    && !goalRefsEqual(reservedGoalRef, currentGoalRef)
  ) {
    return { kind: "reject", code: "stale_goal_instance" };
  }
  return {
    kind: "commit",
    gate: openSourceTurnEffectGate(reservedGoalRef),
  };
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
