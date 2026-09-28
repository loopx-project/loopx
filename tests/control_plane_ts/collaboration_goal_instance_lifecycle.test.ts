import assert from "node:assert/strict";
import test from "node:test";

import { decideCollaborationLifecycle } from "../../loopx/control_plane/collaboration/goal_instance_lifecycle.ts";

const INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const GOAL_A = { goal_id: "delivery", goal_instance_id: INSTANCE_A };
const GOAL_B = { goal_id: "delivery", goal_instance_id: INSTANCE_B };

function facts(
  operation: string,
  patch: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    profile_id: "source_session_v1",
    operation,
    caller_goal_ref: GOAL_A,
    current_goal_ref: GOAL_A,
    record_goal_ref: GOAL_A,
    route_goal_ref: GOAL_A,
    initial_delivery_proved: true,
    ...patch,
  };
}

test("legacy registries preserve the existing lifecycle without parsing refs", () => {
  assert.deepEqual(
    decideCollaborationLifecycle({
      profile_id: null,
      operation: "result_publish",
      caller_goal_ref: "legacy",
    }),
    { kind: "legacy" },
  );
  assert.deepEqual(
    decideCollaborationLifecycle({
      profile_id: "future_profile",
      operation: "result_publish",
    }),
    { kind: "reject", code: "unsupported_profile" },
  );
});

test("request creation requires the captured instance to remain current", () => {
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("request_create", {
        record_goal_ref: null,
        route_goal_ref: null,
      }),
    ),
    { kind: "allow", mode: "current_instance", goal_ref: GOAL_A },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("request_create", {
        current_goal_ref: GOAL_B,
        record_goal_ref: null,
        route_goal_ref: null,
      }),
    ),
    { kind: "reject", code: "stale_goal_instance" },
  );
});

test("current inbox observations omit unstamped and other-instance records", () => {
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("inbox_observe", { record_goal_ref: null }),
    ),
    { kind: "omit", code: "legacy_unbound" },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("inbox_observe", { record_goal_ref: GOAL_B }),
    ),
    { kind: "omit", code: "different_goal_instance" },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("inbox_observe", { current_goal_ref: GOAL_B }),
    ),
    { kind: "omit", code: "different_goal_instance" },
  );
});

test("current mutations reject after recreation", () => {
  for (const operation of [
    "read_record",
    "receiver_decide",
    "artifact_link",
    "peer_return_consume",
  ]) {
    assert.deepEqual(
      decideCollaborationLifecycle(
        facts(operation, { current_goal_ref: GOAL_B }),
      ),
      { kind: "reject", code: "historical_mutation_forbidden" },
    );
  }
});

test("late results stay bound to their saved exact route", () => {
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("result_publish", { current_goal_ref: GOAL_B }),
    ),
    { kind: "allow", mode: "historical_result", goal_ref: GOAL_A },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("result_publish", {
        current_goal_ref: GOAL_B,
        route_goal_ref: GOAL_B,
      }),
    ),
    { kind: "reject", code: "route_instance_mismatch" },
  );
});

test("original return admission requires trusted initial delivery evidence", () => {
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("original_return_admit", {
        current_goal_ref: GOAL_B,
        initial_delivery_proved: false,
      }),
    ),
    { kind: "reject", code: "initial_delivery_unproved" },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("original_return_admit", { current_goal_ref: GOAL_B }),
    ),
    { kind: "allow", mode: "historical_result", goal_ref: GOAL_A },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("original_return_settle", { current_goal_ref: GOAL_B }),
    ),
    { kind: "allow", mode: "historical_result", goal_ref: GOAL_A },
  );
});

test("historical inspection remains read-only", () => {
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("history_inspect", {
        current_goal_ref: GOAL_B,
        route_goal_ref: null,
      }),
    ),
    { kind: "allow", mode: "historical_read", goal_ref: GOAL_A },
  );
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("history_inspect", {
        caller_goal_ref: GOAL_B,
        current_goal_ref: GOAL_B,
      }),
    ),
    { kind: "reject", code: "record_instance_mismatch" },
  );
});

test("malformed strict facts fail closed", () => {
  assert.deepEqual(
    decideCollaborationLifecycle(
      facts("receiver_decide", { caller_goal_ref: null }),
    ),
    { kind: "reject", code: "missing_exact_goal_ref" },
  );
  assert.throws(() =>
    decideCollaborationLifecycle({
      profile_id: "source_session_v1",
      operation: "unknown",
    })
  );
});
