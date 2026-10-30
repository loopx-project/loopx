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
  const policy = hostRetryPolicies[kind];
  if (record.retryable !== (policy !== undefined))
    fail("host failure record retryability does not match its kind");
  const expected: JsonObject = {schema_version: "loopx_turn_host_failure_v0", kind,
    attempt: exact !== null && exact > BigInt(Number.MAX_SAFE_INTEGER) ? String(exact) : attempt,
    retryable: policy !== undefined};
  if (policy) {
    expected.retry = {strategy: "same_configuration", max_attempts: policy[0],
      backoff_seconds: Math.min(policy[1] * 2 ** Math.max(0, Number(attempt) - 1), 300)};
    if (!isDeepStrictEqual(record.retry, expected.retry)) fail("host failure record has an invalid retry policy");
  } else if (Object.hasOwn(record, "retry")) {
    fail("non-retryable host failure must not declare retry policy");
  }
  if (Object.keys(record).length !== Object.keys(expected).length
      || Object.keys(record).some(key => !Object.hasOwn(expected, key)))
    fail("host failure record contains unsupported fields");
  return expected;
}
