import assert from "node:assert/strict";
import test from "node:test";

import { decideCollaborationLifecycle, proveOriginalRequestDelivery } from "../../loopx/control_plane/collaboration/goal_instance_lifecycle.ts";

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
    initial_delivery: committedDelivery("completed"),
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
        initial_delivery: null,
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

function committedDelivery(status: string, exact = true) {
  const request = {
    request_id: "a".repeat(64), goal_id: "delivery", agent_id: "builder", source_id: "web:source",
    ...(exact ? { goal_ref: GOAL_A } : {}),
  };
  return {
    request,
    route: { ...request, client_turn_id: "question" },
    turn: { status, client_turn_id: "question", context_handoff_receipt: null as unknown },
    authorized_source_id: request.source_id,
  };
}

test("a committed handoff survives a lost answer on a settled originating Turn", () => {
  for (const exact of [true, false]) {
    for (const status of ["completed", "failed", "timed_out", "interrupted"]) {
      assert.deepEqual(proveOriginalRequestDelivery(committedDelivery(status, exact)), {
        kind: "proved", basis: "committed_request",
      });
    }
    for (const status of ["queued", "starting", "running", "completing", "interrupting", "unknown"]) {
      assert.deepEqual(proveOriginalRequestDelivery(committedDelivery(status, exact)), {
        kind: "unproved", reason: "originating_turn_unsettled",
      });
    }
  }
});

test("lost-answer recovery cannot substitute a source, request, instance or conflicting receipt", () => {
  const base = committedDelivery("failed");
  for (const mutation of [
    { ...base, authorized_source_id: "web:other" },
    { ...base, route: { ...base.route, source_id: "web:other" } },
    { ...base, route: { ...base.route, request_id: "b".repeat(64) } },
    { ...base, route: { ...base.route, goal_ref: GOAL_B } },
    { ...base, route: { ...base.route, client_turn_id: "another-question" } },
    { ...base, turn: { ...base.turn, context_handoff_receipt: {} } },
    { ...base, turn: { ...base.turn, context_handoff_receipt: { ...base.request, goal_ref: GOAL_B } } },
    { ...base, request: { ...base.request, goal_ref: null } },
    null,
  ]) assert.equal(proveOriginalRequestDelivery(mutation).kind, "unproved");
  const withReceipt = { ...base, turn: { ...base.turn, context_handoff_receipt: base.request } };
  assert.deepEqual(proveOriginalRequestDelivery(withReceipt), {
    kind: "proved", basis: "committed_request_and_receipt",
  });
});


test("readback observes a committed active request without permitting its result return", () => {
  for (const profile_id of [null, "source_session_v1"]) {
    const initial_delivery = committedDelivery("running", profile_id !== null);
    const patch = { profile_id, initial_delivery };
    assert.equal(decideCollaborationLifecycle(facts("original_request_inspect", patch)).kind,
      profile_id === null ? "legacy" : "allow");
    assert.deepEqual(decideCollaborationLifecycle(facts("original_return_admit", patch)),
      { kind: "reject", code: "initial_delivery_unproved" });
    assert.deepEqual(decideCollaborationLifecycle(facts("original_request_inspect", {
      ...patch, initial_delivery: { ...initial_delivery, route: { ...initial_delivery.route, client_turn_id: "other" } },
    })), { kind: "reject", code: "initial_delivery_unproved" });
  }
  assert.deepEqual(decideCollaborationLifecycle(facts("original_request_inspect", {
    current_goal_ref: GOAL_B, initial_delivery: committedDelivery("failed"),
  })), { kind: "allow", mode: "historical_read", goal_ref: GOAL_A });
});
