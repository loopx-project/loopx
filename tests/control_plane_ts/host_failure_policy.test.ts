import assert from "node:assert/strict";
import test from "node:test";
import {hostFailureObservationRequest, hostFailureRecordRequest, hostFailureRetryRequest, normalizeHostFailure}
  from "../../loopx/control_plane/turn_driver/host_failure.ts";

test("native records and journal qualification reuse the same allocation", () => {
  for (const [kind, ceiling, delay] of [["executor_timeout", 2, 5], ["provider_capacity", 3, 30],
    ["provider_overloaded", 3, 30], ["rate_limited", 3, 60], ["transport_lost", 3, 10]] as const) {
    for (const attempt of [1, 2, 4, 10]) {
      const record = hostFailureRecordRequest({kind, attempt_text: String(attempt)});
      assert.deepEqual(record, {schema_version: "loopx_turn_host_failure_v0", kind, attempt,
        retryable: true, retry: {strategy: "same_configuration", max_attempts: ceiling,
          backoff_seconds: Math.min(delay * 2 ** (attempt - 1), 300)}});
      assert.deepEqual(normalizeHostFailure(record), record);
    }
  }
});

test("automatic availability preserves explicit repair and rejects exhausted policies", () => {
  for (const kind of ["auth_failed", "unknown", "output_budget_exhausted", "provider_capacity"]) {
    for (const attempt of [1, 3]) for (const retry_failed of [true, false]) {
      const failure = hostFailureRecordRequest({kind, attempt_text: String(attempt)});
      const request = {failure, retry_failed};
      const legacy = hostFailureRetryRequest(request);
      assert.deepEqual(hostFailureRetryRequest({...request, automatic_only: false}), legacy);
      assert.equal(hostFailureRetryRequest({...request, automatic_only: true}).allowed,
        kind === "provider_capacity" && attempt === 1 && retry_failed);
      assert.equal(legacy.allowed, retry_failed && kind !== "output_budget_exhausted"
        && (kind !== "provider_capacity" || attempt === 1));
    }
  }
});

test("exact native integers do not change the historical no-token journal wire", () => {
  const exact = "9007199254740993";
  const record = hostFailureRecordRequest({kind: "auth_failed", attempt_text: exact});
  assert.equal(record.attempt, exact);
  const decoded = {...record, attempt: Number(exact)};
  assert.equal(normalizeHostFailure(decoded).attempt, Number(exact));
  assert.equal(normalizeHostFailure(decoded, exact).attempt, exact);
});

test("RPC shape rejects malformed attempts and caller-controlled retry fields", () => {
  for (const attempt_text of [true, 1, null, "0", "-1", "1.0", "bad"]) {
    assert.throws(() => hostFailureRecordRequest({kind: "rate_limited", attempt_text}), /positive integer/);
  }
  assert.throws(() => hostFailureRecordRequest({kind: "rate_limited", attempt_text: "1", max_attempts: 99}),
    /unsupported fields/);
  const failure = hostFailureRecordRequest({kind: "rate_limited", attempt_text: "1"});
  for (const automatic_only of [null, "true", 1]) {
    assert.throws(() => hostFailureRetryRequest({failure, retry_failed: true, automatic_only}), /explicit retry intent/);
  }
});

test("observed native phases cannot turn private or forged receipts into a retry", () => {
  const request = {session_id: "session", prior_session_id: "session", transport_ok: true, exit_zero: false,
    ok: false, timed_out: false, error_kind: false, has_host_failure: true, attempt_text: "2",
    native_failure: hostFailureRecordRequest({kind: "provider_capacity", attempt_text: "1"})};
  const observed = hostFailureObservationRequest(request);
  assert.deepEqual(observed.failure, hostFailureRecordRequest({kind: "provider_capacity", attempt_text: "2"}));
  for (const native_failure of [{attempt: 1, kind: "provider_capacity"},
    {...request.native_failure, retryable: false}, {...request.native_failure, attempt: 2}]) {
    assert.equal((hostFailureObservationRequest({...request, native_failure}).failure as {kind: string}).kind, "unknown");
  }
  assert.equal((hostFailureObservationRequest({...request, session_id: "replacement"}).failure as {kind: string}).kind, "unknown");
  const success = {...request, ok: true, exit_zero: true, has_host_failure: false, attempt_text: null};
  assert.deepEqual(hostFailureObservationRequest(success), {failure: null, session_id: "session"});
});
