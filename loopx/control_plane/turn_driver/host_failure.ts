/** Qualification shared by journal recovery and continuation; no host IO. */
import { isDeepStrictEqual } from "node:util";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, type JsonObject } from "../runtime_decode.ts";

const hostFailureKinds: ReadonlySet<string> = new Set([
  "auth_failed", "contract_rejected", "executor_timeout", "output_budget_exhausted",
  "provider_capacity", "provider_overloaded", "quota_exhausted", "rate_limited",
  "session_missing", "transport_lost", "unknown",
]);
const hostRetryPolicies: Readonly<Record<string, readonly [number, number]>> = {
  executor_timeout: [2, 5], provider_capacity: [3, 30], provider_overloaded: [3, 30],
  rate_limited: [3, 60], transport_lost: [3, 10],
};

/** One retry allocation owner for native records and journal qualification. */
function hostFailureRecord(kind: string, attempt: bigint): JsonObject {
  const policy = hostRetryPolicies[kind];
  return {schema_version: "loopx_turn_host_failure_v0", kind,
    attempt: attempt > BigInt(Number.MAX_SAFE_INTEGER) ? String(attempt) : Number(attempt),
    retryable: policy !== undefined,
    ...(policy === undefined ? {} : {retry: {strategy: "same_configuration", max_attempts: policy[0],
      backoff_seconds: Math.min(policy[1] * 2 ** Math.max(0, Number(attempt) - 1), 300)}})};
}

export function hostFailureRecordRequest(params: JsonObject): JsonObject {
  if (Object.keys(params).some(key => !["kind", "attempt_text"].includes(key)))
    throw new EffectRuntimeRequestError("host failure record request contains unsupported fields");
  const kind = params.kind;
  if (typeof kind !== "string" || !hostFailureKinds.has(kind))
    throw new EffectRuntimeRequestError("unsupported built-in host failure kind");
  const encoded = params.attempt_text;
  if (typeof encoded !== "string" || !/^\d+$/.test(encoded) || BigInt(encoded) < 1n)
    throw new EffectRuntimeRequestError("host failure attempt must be a positive integer");
  return hostFailureRecord(kind, BigInt(encoded));
}

/** Observe one configured native Host phase. Success is transport confirmation,
 * never a Turn completion, scheduler ACK or settlement authorization.
 */
export function hostFailureObservationRequest(params: JsonObject): JsonObject {
  const session = typeof params.session_id === "string" ? params.session_id : null;
  const prior = typeof params.prior_session_id === "string" ? params.prior_session_id : null;
  const sameSession = prior === null || prior === session;
  const transported = params.transport_ok === true && params.timed_out === false && params.error_kind === false;
  if (transported && params.exit_zero === true && params.ok === true &&
      params.has_host_failure === false && session && sameSession) {
    return {failure: null, session_id: session};
  }
  let kind = "unknown";
  if (transported && params.exit_zero === false && params.ok === false && sameSession &&
      jsonObject(params.native_failure)?.attempt === 1) {
    try {
      kind = normalizeHostFailure(params.native_failure).kind as string;
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      // Malformed provider metadata is an unknown phase outcome, not a retry.
    }
  }
  return {failure: hostFailureRecordRequest({kind, attempt_text: params.attempt_text}),
    session_id: prior ?? session};
}

export function normalizeHostFailure(value: unknown, integerAttempt?: string | null): JsonObject {
  const fail = (message: string): never => { throw new EffectRuntimeRequestError(message); };
  const record = jsonObject(value) ?? fail("host failure record must be one object");
  if (record.schema_version !== "loopx_turn_host_failure_v0")
    fail("host failure record has an unsupported schema");
  const kind = typeof record.kind === "string" ? record.kind : "";
  if (!hostFailureKinds.has(kind)) fail("unsupported built-in host failure kind");
  const attempt = record.attempt;
  const exact = integerAttempt === undefined ? null
    : integerAttempt !== null && /^\d+$/.test(integerAttempt) ? BigInt(integerAttempt) : 0n;
  if (exact !== null ? exact < 1n || Number(exact) !== attempt
    : typeof attempt !== "number" || !Number.isInteger(attempt) || attempt < 1)
    fail("host failure record has an invalid attempt");
  if (typeof record.retryable !== "boolean") fail("host failure record has an invalid retryable marker");
  const expected: JsonObject = {...hostFailureRecord(kind, exact ?? BigInt(attempt as number)),
    // Historical TS callers without exact IO tokens retain their number wire.
    attempt: exact !== null && exact > BigInt(Number.MAX_SAFE_INTEGER) ? String(exact) : attempt};
  if (record.retryable !== expected.retryable)
    fail("host failure record retryability does not match its kind");
  if (expected.retryable) {
    if (!isDeepStrictEqual(record.retry, expected.retry)) fail("host failure record has an invalid retry policy");
  } else if (Object.hasOwn(record, "retry")) {
    fail("non-retryable host failure must not declare retry policy");
  }
  if (Object.keys(record).length !== Object.keys(expected).length
      || Object.keys(record).some(key => !Object.hasOwn(expected, key)))
    fail("host failure record contains unsupported fields");
  return expected;
}

/** The same retry rule for an explicit Turn retry and an outer Host phase.
 * This decides failure eligibility only, never session, budget or launch authority.
 */
export function hostFailureRetryCheck(failure: JsonObject, retryFailed: boolean): JsonObject | null {
  // Both callers normalize with their existing transport owner first. Journal
  // decoding may retain a large integral attempt as its exact decimal string.
  if (!retryFailed) return {kind: "host_retry_policy", outcome: "failed",
    reason: "failed_retry_not_requested"};
  if (failure.retryable === true) {
    const retry = failure.retry as JsonObject;
    return Number(failure.attempt) >= Number(retry.max_attempts)
      ? {kind: "host_retry_policy", outcome: "failed", reason: "host_retry_budget_exhausted"}
      : {kind: "host_retry_policy", outcome: "passed"};
  }
  // Preserve the Journal's explicit-repair behavior. Output exhaustion has no
  // proved whole-Turn remainder, so even an explicit blind replay is refused.
  return failure.kind === "output_budget_exhausted"
    ? {kind: "host_retry_policy", outcome: "failed", reason: "host_retry_not_available"}
    : null;
}

export function hostFailureRetryRequest(params: JsonObject): JsonObject {
  if (typeof params.retry_failed !== "boolean" || Object.keys(params).some(
    key => !["failure", "retry_failed", "automatic_only"].includes(key)) ||
      (Object.hasOwn(params, "automatic_only") && typeof params.automatic_only !== "boolean"))
    throw new EffectRuntimeRequestError("Host retry requires an explicit retry intent");
  const failure = normalizeHostFailure(params.failure);
  const check = hostFailureRetryCheck(failure, params.retry_failed);
  return {allowed: params.automatic_only === true
    ? failure.retryable === true && check?.outcome === "passed"
    : check === null || check.outcome === "passed", check};
}
