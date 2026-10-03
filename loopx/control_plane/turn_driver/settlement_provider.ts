/** Pure admission and recovery rules for the existing Turn settlement providers. */
import {
  isCommittedPayload,
  TURN_PROVIDER_STEP_KINDS,
  type JsonObject,
  type SettlementFailure,
  type TurnProviderStepKind,
} from "../effect_program.ts";
import { requireJsonObject, requireNonEmptyString, requireStringLiteral } from "../runtime_decode.ts";

export type ProviderStepKind = TurnProviderStepKind;

const PROVIDER_RESOLUTION_KINDS = ["committed", "absent", "unknown"] as const;
type ProviderResolutionKind = (typeof PROVIDER_RESOLUTION_KINDS)[number];


export interface PreparedEffectAttempt {
  status: "prepared";
  effect_ref: string;
}

export interface ProviderObservation {
  kind: ProviderResolutionKind;
  payload: JsonObject | null;
  reason: string | null;
}

export interface ProviderAttemptResult {
  step_kind: ProviderStepKind;
  payload: JsonObject;
}

export interface ProviderEffectContext {
  step_kind: ProviderStepKind;
  effect_ref: string;
  completed_phases: readonly string[];
}

export type ProviderEffect = ProviderEffectContext & (
  | { action: "prepare_and_execute" | "execute_prepared" }
  | { action: "resolve_prepared" }
  | { action: "checkpoint" | "abort_prepared"; payload: JsonObject }
);

type ProviderDecision =
  | { effect: ProviderEffect; failure: null }
  | { effect: null; failure: SettlementFailure };

export function decodeProviderAttemptResult(
  value: unknown,
  label: string,
): ProviderAttemptResult | null {
  if (value === null || value === undefined) return null;
  const attempt = requireJsonObject(value, label);
  return {
    step_kind: requireStringLiteral(
      attempt.step_kind,
      TURN_PROVIDER_STEP_KINDS,
      `${label}.step_kind`,
    ),
    payload: requireJsonObject(
      attempt.payload,
      `${label}.payload`,
    ),
  };
}

export function decodeProviderRecord<Value>(
  value: unknown,
  label: string,
  decode: (value: unknown, label: string) => Value,
): Partial<Record<ProviderStepKind, Value>> {
  if (value === null || value === undefined) return {};
  const record = requireJsonObject(value, label);
  const decoded: Partial<Record<ProviderStepKind, Value>> = {};
  for (const [rawStep, rawValue] of Object.entries(record)) {
    const step = requireStringLiteral(
      rawStep,
      TURN_PROVIDER_STEP_KINDS,
      `${label} step`,
    );
    decoded[step] = decode(rawValue, `${label}.${step}`);
  }
  return decoded;
}

export function decodePreparedAttempt(
  value: unknown,
  label: string,
): PreparedEffectAttempt {
  const attempt = requireJsonObject(value, label);
  return {
    status: requireStringLiteral(
      attempt.status,
      ["prepared"] as const,
      `${label}.status`,
    ),
    effect_ref: requireNonEmptyString(attempt.effect_ref, `${label}.effect_ref`),
  };
}

export function decodeProviderObservation(
  value: unknown,
  label: string,
): ProviderObservation {
  const observation = requireJsonObject(value, label);
  // This is external provider evidence. Unsupported observations cannot prove
  // absence or rejection and must retain the prepared operation for readback.
  const kind = observation.kind;
  if (kind !== "committed" && kind !== "absent" && kind !== "unknown") {
    return { kind: "unknown", payload: null, reason: "Unsupported provider readback kind" };
  }
  const payload = observation.payload;
  if (kind === "absent" && payload !== null && payload !== undefined) {
    return { kind: "unknown", payload: null, reason: "Absent provider readback must not contain a payload" };
  }
  return {
    kind,
    payload: payload !== null && typeof payload === "object" && !Array.isArray(payload)
      ? payload as JsonObject : null,
    reason: typeof observation.reason === "string" && observation.reason.trim()
      ? observation.reason : null,
  };
}

/** Omitted refs retain legacy compatibility; explicit refs must name this effect. */
export function providerPayloadMatchesRef(payload: JsonObject | null, effectRef: string): boolean {
  return payload === null || !("effect_ref" in payload) || payload.effect_ref === effectRef;
}

/** Authorize exactly one IO action from the next ordered provider and its facts. */
export function settlementProviderAction(
  effect: ProviderEffectContext,
  attempts: Partial<Record<ProviderStepKind, PreparedEffectAttempt>>,
  observations: Partial<Record<ProviderStepKind, ProviderObservation>>,
  returned: ProviderAttemptResult | null,
): ProviderDecision {
  const step = effect.step_kind;
  const fail = (kind: SettlementFailure["kind"], reason: string, failedStep = step): ProviderDecision => ({
    effect: null, failure: { kind, step_kind: failedStep, reason },
  });
  const prepared = Object.entries(attempts);
  const observed = Object.keys(observations);
  if (prepared.length === 0) {
    return observed.length > 0 || returned !== null
      ? fail("receipt_missing", "Turn settlement has a provider observation without a prepared effect")
      : { effect: { ...effect, action: "prepare_and_execute" }, failure: null };
  }
  if (prepared.length !== 1 || prepared[0][0] !== step) {
    return fail("receipt_missing", "Prepared settlement effects do not match the next ordered provider",
      (prepared.find(([key]) => key !== step)?.[0] ?? step) as ProviderStepKind);
  }
  if (prepared[0][1].effect_ref !== effect.effect_ref) {
    return fail("identity_mismatch", "Prepared settlement effect does not match the current operation");
  }
  if (observed.some((key) => key !== step) || (returned !== null && returned.step_kind !== step)) {
    return fail("receipt_missing", "Provider observation does not match the prepared settlement effect");
  }
  const observation = observations[step];
  if (returned !== null && observation !== undefined) {
    return fail("receipt_missing", "Provider return and readback cannot authorize the same action");
  }
  if (returned === null && observation === undefined) {
    return { effect: { ...effect, action: "resolve_prepared" }, failure: null };
  }
  if (observation?.kind === "unknown") {
    return fail("effect_outcome_unknown", observation.reason ?? "Provider could not resolve the prepared settlement effect");
  }
  if (observation?.kind === "absent") {
    if (observation.payload !== null) {
      return fail("effect_outcome_unknown", "Absent provider readback must not contain a committed payload");
    }
    return { effect: { ...effect, action: "execute_prepared" }, failure: null };
  }
  const payload = returned !== null ? returned.payload : observation?.payload ?? null;
  // Legacy providers may omit the ref; an explicit ref must bind this operation.
  if (!providerPayloadMatchesRef(payload, effect.effect_ref)) {
    return fail("identity_mismatch", "Provider payload does not match the prepared settlement effect");
  }
  if (payload === null || !isCommittedPayload(payload)) {
    return returned !== null
      ? { effect: { ...effect, action: "abort_prepared", payload: returned.payload }, failure: null }
      : fail("receipt_missing", "Provider reported a committed settlement effect without a durable committed payload");
  }
  return { effect: { ...effect, action: "checkpoint", payload }, failure: null };
}
