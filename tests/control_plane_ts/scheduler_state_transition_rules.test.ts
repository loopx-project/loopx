import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  evaluateSchedulerStateTransition,
  SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
  SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
} from "../../loopx/control_plane/scheduler/state_transition_rules.ts";

const fixture = JSON.parse(
  readFileSync(
    new URL(
      "../fixtures/control_plane/scheduler_state_transition_characterization_v0.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  schema_version: string;
  source_baseline: string;
  cases: Array<{
    name: string;
    request: Record<string, unknown>;
    expected_result: Record<string, unknown>;
  }>;
};

test("settled replay preserves observed cadence without deciding a future schedule", () => {
  for (const observed of [null, "FREQ=MINUTELY;INTERVAL=3", "FREQ=MINUTELY;INTERVAL=60"]) {
    const request = { schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      operation: "settled_replay", observed_host_rrule: observed };
    const before = structuredClone(request);
    const result = evaluateSchedulerStateTransition(request);
    assert.equal(result.operation, "settled_replay");
    const hint = result.hint as Record<string, unknown>;
    assert.equal(hint.action, "preserve_current_schedule");
    assert.equal(hint.next_trigger, "fresh_turn_identity");
    assert.equal(hint.reset_policy, undefined);
    const host = hint.app_automation as Record<string, unknown>;
    assert.equal(host.observed_host_rrule, observed);
    assert.equal(host.host_action, "none");
    assert.equal(host.ack_required, false);
    for (const field of ["recommended_rrule", "recommended_interval_minutes", "stateful_backoff",
                        "ack_hint", "failure_hint", "fallback_hint"]) {
      assert.equal(host[field], undefined, field);
    }
    assert.deepEqual(request, before);
  }
  assert.throws(() => evaluateSchedulerStateTransition({
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    operation: "settled_replay", observed_host_rrule: 3,
  }), /observed_host_rrule must be a string/);
});

test("pinned Python scheduler transition characterization remains exact", () => {
  assert.equal(
    fixture.schema_version,
    "loopx_scheduler_state_transition_characterization_v0",
  );
  assert.equal(fixture.source_baseline, "3f923c4fe");
  assert.equal(fixture.cases.length, 19);
  for (const item of fixture.cases) {
    const result = evaluateSchedulerStateTransition({
      schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      ...item.request,
    });
    assert.deepEqual(
      result,
      {
        schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
        ...item.expected_result,
      },
      item.name,
    );
  }
});

test("runtime boundary rejects malformed transition facts", () => {
  assert.throws(
    () => evaluateSchedulerStateTransition({
      schema_version: "unsupported",
      operation: "cadence",
    }),
    /request schema mismatch/,
  );
  assert.throws(
    () => evaluateSchedulerStateTransition({
      schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      operation: "cadence",
      progression_size: 0,
      state_present: false,
    }),
    /must not be empty/,
  );
  assert.throws(
    () => evaluateSchedulerStateTransition({
      schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      operation: "host",
      state_status: "same_identity",
      observed_host_rrule_present: "yes",
    }),
    /must be a boolean/,
  );
});

test("transition decisions do not mutate caller input", () => {
  const request = {
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    ...fixture.cases[2].request,
  };
  const before = structuredClone(request);
  evaluateSchedulerStateTransition(request);
  assert.deepEqual(request, before);
});

test("monitor schedule materializes cadence and preserves explicit due time", () => {
  const cadence = evaluateSchedulerStateTransition({
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    operation: "monitor_schedule",
    generated_at: "2026-08-25T10:00:00+08:00",
    cadence: "30 minutes",
    explicit_next_due_at: null,
  });
  assert.deepEqual(cadence, {
    schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
    operation: "monitor_schedule",
    next_due_at: "2026-08-25T02:30:00Z",
    schedule_source: "cadence",
    cadence_seconds: 1800,
  });

  const explicit = evaluateSchedulerStateTransition({
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    operation: "monitor_schedule",
    generated_at: "ignored-for-explicit-schedule",
    cadence: "1h",
    explicit_next_due_at: "2026-08-26T09:00:00+08:00",
  });
  assert.equal(explicit.next_due_at, "2026-08-26T09:00:00+08:00");
  assert.equal(explicit.schedule_source, "explicit");
  assert.equal(explicit.cadence_seconds, 3600);

  const missing = evaluateSchedulerStateTransition({
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    operation: "monitor_schedule",
    generated_at: "2026-08-25T10:00:00+08:00",
    cadence: "not-a-cadence",
  });
  assert.equal(missing.next_due_at, null);
  assert.equal(missing.schedule_source, "none");
});

test("monitor schedule rejects malformed timestamps without wall-clock fallback", () => {
  assert.throws(
    () => evaluateSchedulerStateTransition({
      schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      operation: "monitor_schedule",
      generated_at: "not-a-timestamp",
      cadence: "30m",
    }),
    /generated_at must be an ISO timestamp/,
  );
  assert.throws(
    () => evaluateSchedulerStateTransition({
      schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      operation: "monitor_schedule",
      generated_at: "2026-08-25T10:00:00Z",
      explicit_next_due_at: "not-a-timestamp",
    }),
    /explicit_next_due_at must be an ISO timestamp/,
  );
});

function backoffRequest(extra: Record<string, unknown> = {}) {
  return {
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    operation: "backoff",
    progression_minutes: [15, 30, 60],
    scheduler_state: {},
    reset_token: "reset-token",
    identity_signature: "identity-signature",
    advance_same_identity: true,
    current_time: "2026-01-01T12:10:00Z",
    observed_host_rrule: "",
    cadence_class: "monitor_wait",
    stale_tolerance_minutes: 5,
    ...extra,
  };
}

test("coarse backoff transition owns cadence, retention, and host decision", () => {
  const settled = evaluateSchedulerStateTransition(backoffRequest({
    scheduler_state: {
      progression_index: 1,
      last_applied_rrule: "FREQ=MINUTELY;INTERVAL=30",
      reset_token: "reset-token",
      identity_signature: "identity-signature",
      updated_at: "2026-01-01T12:00:00Z",
    },
    observed_host_rrule: "RRULE:FREQ=MINUTELY;INTERVAL=30",
  }));
  assert.equal(settled.current_index, 1);
  assert.equal(settled.cadence_transition, "hold_until_interval");
  assert.equal(settled.current_rrule, "FREQ=MINUTELY;INTERVAL=30");
  assert.equal(settled.host_transition, "settled");
  assert.equal(settled.current_rrule_already_applied, true);

  const failedPair = evaluateSchedulerStateTransition(backoffRequest({
    scheduler_state: {
      progression_index: 0,
      last_applied_rrule: "FREQ=MINUTELY;INTERVAL=60",
      reset_token: "reset-token",
      identity_signature: "identity-signature",
      updated_at: "2026-01-01T12:00:00Z",
      host_update_failures: [{
        schema_version: "scheduler_host_update_failure_v0",
        target_rrule: "FREQ=MINUTELY;INTERVAL=15",
        observed_host_rrule: "FREQ=MINUTELY;INTERVAL=60",
        failure_kind: "timeout",
        failure_count: 1,
        failed_at: "2026-01-01T11:30:00Z",
      }],
    },
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=60",
  }));
  assert.equal(failedPair.current_index, 0);
  assert.equal(failedPair.cadence_transition, "retry_unacknowledged_failure");
  assert.equal(failedPair.host_transition, "recorded_failure_suppressed");
  assert.equal(failedPair.repeated_failed_pair, true);
  assert.equal(
    (failedPair.recorded_host_failure as Record<string, unknown>).failure_count,
    1,
  );
});

test("coarse backoff boundary rejects malformed state facts", () => {
  assert.throws(
    () => evaluateSchedulerStateTransition(backoffRequest({
      progression_minutes: [15, 0],
    })),
    /positive integers/,
  );
  assert.throws(
    () => evaluateSchedulerStateTransition(backoffRequest({
      scheduler_state: [],
    })),
    /scheduler_state must be an object/,
  );
  assert.throws(
    () => evaluateSchedulerStateTransition(backoffRequest({
      stale_tolerance_minutes: -1,
    })),
    /must not be negative/,
  );
});
